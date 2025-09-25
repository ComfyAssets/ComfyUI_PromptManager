"""PromptTracker implementation for robust prompt-to-image tracking.

This module maintains the association between PromptManager nodes and
generated images by tracking execution flow through ComfyUI's pipeline.
"""

import asyncio
import json
import threading
import time
from uuid import uuid4
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

# Import with fallbacks
try:
    from promptmanager.loggers import get_logger  # type: ignore
except ImportError:  # pragma: no cover
    from loggers import get_logger  # type: ignore

logger = get_logger("promptmanager.tracking.prompt_tracker")


@dataclass
class TrackingData:
    """Data structure for tracking prompt execution."""
    
    node_id: str
    unique_id: str
    prompt_text: str
    negative_prompt: str = ""
    workflow_data: Dict[str, Any] = field(default_factory=dict)
    execution_id: str = field(default_factory=lambda: str(uuid4()))
    timestamp: float = field(default_factory=time.time)
    metadata: Dict[str, Any] = field(default_factory=dict)
    connected_nodes: Set[str] = field(default_factory=set)
    images_generated: List[str] = field(default_factory=list)
    confidence_score: float = 1.0


class PromptTracker:
    """Tracks prompts through ComfyUI execution pipeline.
    
    This class maintains the association between PromptManager nodes
    and the images they generate, handling multiple nodes and concurrent
    executions correctly.
    """
    
    def __init__(self, db_path: str = "prompts.db"):
        """Initialize the PromptTracker.

        Args:
            db_path: Path to the database file
        """
        self.db_path = db_path
        self.db_instance = None  # Will be set by get_prompt_tracker if provided

        # Thread-safe tracking storage
        self._lock = threading.RLock()
        self._active_prompts: Dict[str, TrackingData] = {}
        self._execution_graph: Dict[str, Set[str]] = {}
        self._node_outputs: Dict[str, Any] = {}

        # Metrics for validation
        self.metrics = {
            "total_tracked": 0,
            "successful_pairs": 0,
            "failed_pairs": 0,
            "multi_node_workflows": 0,
            "avg_confidence": 0.0,
        }

        logger.info("PromptTracker initialized")
    
    def register_prompt(
        self,
        node_id: str,
        unique_id: str,
        prompt: str,
        negative_prompt: str = "",
        workflow: Optional[Dict] = None,
        extra_data: Optional[Dict] = None
    ) -> str:
        """Register a prompt from a PromptManager node.

        Args:
            node_id: The node's ID in the workflow
            unique_id: ComfyUI's unique execution ID
            prompt: The positive prompt text
            negative_prompt: The negative prompt text
            workflow: The complete workflow data
            extra_data: Additional metadata

        Returns:
            Execution ID for this prompt
        """
        with self._lock:
            logger.info(f"🔵 register_prompt called: node_id={node_id}, unique_id={unique_id} (type={type(unique_id)})")
            logger.debug(f"  Prompt text: {prompt[:50]}...")

            # Clean up old entries when a new workflow starts
            # If we have entries and this is a new unique_id, it's likely a new workflow
            if self._active_prompts and unique_id not in self._active_prompts:
                # Check if all existing entries are old (> 60 seconds)
                current_time = time.time()
                all_old = all(current_time - t.timestamp > 60 for t in self._active_prompts.values())
                if all_old:
                    logger.info(f"New workflow detected (unique_id={unique_id}), clearing {len(self._active_prompts)} old entries")
                    self._active_prompts.clear()
                    self._execution_graph.clear()

            # Log the state before registration
            before_keys = list(self._active_prompts.keys())
            logger.debug(f"  Keys before registration: {before_keys}")

            tracking = TrackingData(
                node_id=node_id,
                unique_id=unique_id,
                prompt_text=prompt,
                negative_prompt=negative_prompt,
                workflow_data=workflow or {},
                metadata=extra_data or {}
            )

            # Store by unique_id for retrieval during save
            self._active_prompts[unique_id] = tracking

            # Log the state after registration
            after_keys = list(self._active_prompts.keys())
            logger.info(f"🟢 Stored prompt with unique_id={unique_id} in _active_prompts")
            logger.debug(f"  Keys after registration: {after_keys}")

            # Verify storage
            if unique_id in self._active_prompts:
                logger.info(f"✅ Verification: unique_id={unique_id} IS in _active_prompts")
            else:
                logger.error(f"❌ Verification FAILED: unique_id={unique_id} NOT in _active_prompts!")
            
            # Update execution graph
            if node_id not in self._execution_graph:
                self._execution_graph[node_id] = set()
            
            # Update metrics
            self.metrics["total_tracked"] += 1
            
            # Count multi-node workflows
            prompt_nodes = [k for k in self._execution_graph.keys() 
                          if k.startswith("PromptManager")]
            if len(prompt_nodes) > 1:
                self.metrics["multi_node_workflows"] += 1
            
            logger.debug(f"Registered prompt from node {node_id} with ID {unique_id}")
            logger.info(f"Registered tracking: unique_id={unique_id} node_id={node_id} len_active={len(self._active_prompts)}")
            return tracking.execution_id
    
    def register_connection(self, from_node: str, to_node: str) -> None:
        """Register a connection between nodes in the execution graph.
        
        Args:
            from_node: Source node ID
            to_node: Destination node ID
        """
        with self._lock:
            if from_node not in self._execution_graph:
                self._execution_graph[from_node] = set()
            self._execution_graph[from_node].add(to_node)
            
            # Update connected nodes for active prompts
            for tracking in self._active_prompts.values():
                if tracking.node_id == from_node:
                    tracking.connected_nodes.add(to_node)
    
    def get_prompt_for_save(
        self,
        save_node_id: str,
        unique_id: Optional[str] = None
    ) -> Optional[TrackingData]:
        """Get the prompt data for a SaveImage node.

        Args:
            save_node_id: The SaveImage node's ID
            unique_id: Optional unique ID if provided by ComfyUI

        Returns:
            TrackingData if found, None otherwise
        """
        with self._lock:
            logger.debug(f"get_prompt_for_save called: save_node_id={save_node_id}, unique_id={unique_id}")
            logger.debug(f"Active prompts count: {len(self._active_prompts)}")

            # Direct lookup if unique_id provided
            if unique_id:
                logger.debug(f"Attempting direct lookup for unique_id={unique_id}")
                logger.debug(f"Available keys in _active_prompts: {list(self._active_prompts.keys())}")

                if unique_id in self._active_prompts:
                    logger.info(f"✅ Direct lookup HIT for unique_id={unique_id}")
                    return self._active_prompts[unique_id]
                else:
                    # Check if it's a type mismatch issue (string vs int)
                    for key in self._active_prompts.keys():
                        if str(key) == str(unique_id):
                            logger.warning(f"Found match with string conversion: key={key} (type={type(key)}), unique_id={unique_id} (type={type(unique_id)})")

                    logger.info(f"❌ Direct lookup MISS for unique_id={unique_id}; active_ids={list(self._active_prompts.keys())}")

            # Fallback: if caller passed a PromptManager node_id as save_node_id, use the most recent entry
            if save_node_id and save_node_id.startswith("PromptManager"):
                candidates = [t for t in self._active_prompts.values() if t.node_id == save_node_id]
                if candidates:
                    best = max(candidates, key=lambda t: t.timestamp)
                    logger.info(f"Fallback matched by node_id={save_node_id}; using latest timestamp")
                    return best
            
            # Otherwise, trace through the graph
            prompt_sources = self._find_prompt_sources(save_node_id)
            
            if not prompt_sources:
                logger.warning(f"No prompt source found for SaveImage {save_node_id}")
                return None
            
            # If multiple sources, use the most recent or highest confidence
            best_match = None
            best_score = 0.0
            
            for source_id in prompt_sources:
                for tracking in self._active_prompts.values():
                    if tracking.node_id == source_id:
                        score = self._calculate_confidence(tracking, save_node_id)
                        if score > best_score:
                            best_score = score
                            best_match = tracking
            
            if best_match:
                best_match.confidence_score = best_score
                logger.debug(f"Found prompt for save {save_node_id} with confidence {best_score:.2f}")
            
            return best_match

    def debug_active_ids(self) -> list:
        """Return a snapshot of active unique_ids for debugging."""
        with self._lock:
            return list(self._active_prompts.keys())
    
    def record_image_saved(
        self,
        image_path: str,
        tracking_data: TrackingData,
        metadata: Optional[Dict] = None
    ) -> None:
        """Record that an image was saved with associated prompt.

        Args:
            image_path: Path to the saved image
            tracking_data: The tracking data for this image
            metadata: Additional metadata to store
        """
        from pathlib import Path

        try:
            normalized_path = str(Path(image_path).expanduser().resolve())
        except Exception:
            normalized_path = str(Path(image_path).absolute())

        with self._lock:
            tracking_data.images_generated.append(normalized_path)

            # Get the prompt_id from the tracking data metadata
            prompt_id = tracking_data.metadata.get('prompt_id')

            if prompt_id:
                # Use the stored database instance if available, otherwise create new one
                if hasattr(self, 'db_instance') and self.db_instance:
                    db = self.db_instance
                    print(f"   📚 Using stored database instance")
                else:
                    from src.database import PromptDatabase
                    db = PromptDatabase()
                    print(f"   ⚠️ Warning: Creating new database instance (not optimal)")

                metadata_payload = {
                    "unique_id": tracking_data.unique_id,
                    "node_id": tracking_data.node_id,
                    "confidence_score": tracking_data.confidence_score,
                    "generation_time": datetime.fromtimestamp(tracking_data.timestamp).isoformat(),
                    "workflow_data": tracking_data.workflow_data,
                }
                if metadata:
                    metadata_payload.update(metadata)

                params = metadata_payload.get("parameters")
                if isinstance(params, dict):
                    params.setdefault("absolute_path", normalized_path)
                    params.setdefault("relative_path", Path(normalized_path).name)
                else:
                    metadata_payload["parameters"] = {"absolute_path": normalized_path}

                # Link the image to the prompt
                db.link_image_to_prompt(
                    prompt_id=prompt_id,
                    image_path=normalized_path,
                    metadata=metadata_payload
                )
                print(f"   💾 Successfully linked image to prompt_id {prompt_id}")
            else:
                logger.warning(f"No prompt_id found in tracking data for image {image_path}")
            
            # Update metrics
            self.metrics["successful_pairs"] += 1
            current_avg = self.metrics["avg_confidence"]
            total = self.metrics["successful_pairs"]
            self.metrics["avg_confidence"] = (
                (current_avg * (total - 1) + tracking_data.confidence_score) / total
            )
            
            logger.info(f"Recorded image {Path(image_path).name} with prompt (confidence: {tracking_data.confidence_score:.2f})")
    
    def _find_prompt_sources(self, target_node: str) -> Set[str]:
        """Find all PromptManager nodes that connect to a target node.
        
        Args:
            target_node: The node to trace back from
            
        Returns:
            Set of PromptManager node IDs
        """
        sources = set()
        visited = set()
        
        def trace_back(node_id: str):
            if node_id in visited:
                return
            visited.add(node_id)
            
            # Check if this is a PromptManager
            if "PromptManager" in node_id:
                sources.add(node_id)
            
            # Find nodes that connect to this one
            for from_node, connections in self._execution_graph.items():
                if node_id in connections:
                    trace_back(from_node)
        
        trace_back(target_node)
        return sources
    
    def _calculate_confidence(
        self,
        tracking: TrackingData,
        save_node: str
    ) -> float:
        """Calculate confidence score for prompt-to-image association.
        
        Args:
            tracking: The tracking data
            save_node: The SaveImage node ID
            
        Returns:
            Confidence score between 0 and 1
        """
        score = 1.0
        
        # Reduce confidence if there are multiple prompt sources
        prompt_sources = self._find_prompt_sources(save_node)
        if len(prompt_sources) > 1:
            score *= 0.8
        
        # Increase confidence if there's a direct connection
        if save_node in tracking.connected_nodes:
            score *= 1.2
        
        # Consider time factor (more recent = higher confidence)
        age = time.time() - tracking.timestamp
        if age > 60:  # Over 1 minute old
            score *= 0.9
        
        # Clamp between 0 and 1
        return min(1.0, max(0.0, score))
    
    def clear_workflow_tracking(self) -> None:
        """Clear all tracking data for a new workflow."""
        with self._lock:
            count = len(self._active_prompts)
            if count > 0:
                logger.info(f"Clearing {count} tracking entries for new workflow")
                self._active_prompts.clear()
                self._execution_graph.clear()
                self._node_outputs.clear()

    def cleanup_old_tracking(self, max_age_seconds: int = 3600) -> int:
        """Clean up old tracking data from abandoned workflows.

        Args:
            max_age_seconds: Maximum age in seconds (default: 3600 = 1 hour)
            
        Returns:
            Number of entries cleaned up
        """
        with self._lock:
            current_time = time.time()
            to_remove = []
            
            for unique_id, tracking in self._active_prompts.items():
                if current_time - tracking.timestamp > max_age_seconds:
                    to_remove.append(unique_id)
            
            for unique_id in to_remove:
                tracking = self._active_prompts.get(unique_id)
                if tracking:
                    age = current_time - tracking.timestamp
                    logger.debug(f"  Removing entry {unique_id} (age: {age:.1f}s, node: {tracking.node_id})")
                del self._active_prompts[unique_id]

            if to_remove:
                logger.info(f"Cleaned up {len(to_remove)} old tracking entries (>= {max_age_seconds}s old)")
            else:
                logger.debug(f"Cleanup check: {len(self._active_prompts)} entries, none older than {max_age_seconds}s")
            
            return len(to_remove)
    
    def get_metrics(self) -> Dict[str, Any]:
        """Get tracking metrics for validation.
        
        Returns:
            Dictionary of metrics
        """
        with self._lock:
            accuracy = 0.0
            if self.metrics["total_tracked"] > 0:
                accuracy = (self.metrics["successful_pairs"] / 
                          self.metrics["total_tracked"]) * 100
            
            return {
                **self.metrics,
                "accuracy_rate": accuracy,
                "active_prompts": len(self._active_prompts),
                "graph_nodes": len(self._execution_graph),
            }
    
    def reset_metrics(self) -> None:
        """Reset tracking metrics."""
        with self._lock:
            self.metrics = {
                "total_tracked": 0,
                "successful_pairs": 0,
                "failed_pairs": 0,
                "multi_node_workflows": 0,
                "avg_confidence": 0.0,
            }
            logger.info("Metrics reset")
    
    async def start_cleanup_task(self) -> None:
        """Start background task for periodic cleanup."""
        while True:
            await asyncio.sleep(60)  # Run every minute
            self.cleanup_old_tracking()
