"""Graph analyzer for workflow connectivity analysis.

This module analyzes ComfyUI workflow graphs to understand the
relationships between PromptManager and SaveImage nodes.
"""

import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

# Import with fallbacks
try:
    from promptmanager.loggers import get_logger  # type: ignore
except ImportError:  # pragma: no cover
    from loggers import get_logger  # type: ignore

logger = get_logger("promptmanager.tracking.graph_analyzer")


class GraphAnalyzer:
    """Analyzes ComfyUI workflow graphs for prompt tracking."""
    
    def __init__(self):
        """Initialize the graph analyzer."""
        self.nodes = {}
        self.edges = defaultdict(set)
        self.reverse_edges = defaultdict(set)
        self.node_types = {}
        self.prompt_managers = set()
        self.save_nodes = set()
        
    def load_workflow(self, workflow: Dict[str, Any]) -> None:
        """Load and analyze a ComfyUI workflow.
        
        Args:
            workflow: ComfyUI workflow dictionary
        """
        self.clear()
        
        # Extract nodes
        nodes_data = workflow.get("nodes", [])
        for node in nodes_data:
            node_id = str(node.get("id", ""))
            node_type = node.get("type", "")
            
            self.nodes[node_id] = node
            self.node_types[node_id] = node_type
            
            # Identify special nodes
            if "PromptManager" in node_type:
                self.prompt_managers.add(node_id)
            elif "SaveImage" in node_type or "Save" in node_type:
                self.save_nodes.add(node_id)
        
        # Extract edges from node inputs
        for node_id, node in self.nodes.items():
            inputs = node.get("inputs", {})
            
            for input_name, input_value in inputs.items():
                if isinstance(input_value, list) and len(input_value) >= 2:
                    # ComfyUI format: [source_node_id, source_output_index]
                    source_node = str(input_value[0])
                    
                    if source_node in self.nodes:
                        self.edges[source_node].add(node_id)
                        self.reverse_edges[node_id].add(source_node)
        
        logger.info(f"Loaded workflow: {len(self.nodes)} nodes, "
                   f"{len(self.prompt_managers)} PromptManagers, "
                   f"{len(self.save_nodes)} SaveNodes")
    
    def find_paths(
        self,
        from_node: str,
        to_node: str,
        max_depth: int = 20
    ) -> List[List[str]]:
        """Find all paths between two nodes.
        
        Args:
            from_node: Starting node ID
            to_node: Target node ID
            max_depth: Maximum path length to search
            
        Returns:
            List of paths (each path is a list of node IDs)
        """
        if from_node not in self.nodes or to_node not in self.nodes:
            return []
        
        paths = []
        queue = deque([(from_node, [from_node])])
        
        while queue:
            current, path = queue.popleft()
            
            if len(path) > max_depth:
                continue
            
            if current == to_node:
                paths.append(path)
                continue
            
            for neighbor in self.edges.get(current, []):
                if neighbor not in path:  # Avoid cycles
                    queue.append((neighbor, path + [neighbor]))
        
        return paths
    
    def find_prompt_sources(self, save_node: str) -> Dict[str, List[List[str]]]:
        """Find all PromptManager nodes that connect to a SaveImage node.
        
        Args:
            save_node: SaveImage node ID
            
        Returns:
            Dictionary mapping PromptManager IDs to paths
        """
        sources = {}
        
        for prompt_node in self.prompt_managers:
            paths = self.find_paths(prompt_node, save_node)
            if paths:
                sources[prompt_node] = paths
        
        return sources
    
    def analyze_connectivity(self) -> Dict[str, Any]:
        """Analyze the overall connectivity of the workflow.
        
        Returns:
            Analysis results including potential issues
        """
        results = {
            "total_nodes": len(self.nodes),
            "prompt_managers": list(self.prompt_managers),
            "save_nodes": list(self.save_nodes),
            "connections": {},
            "issues": [],
            "complexity_score": 0.0
        }
        
        # Analyze each save node
        for save_node in self.save_nodes:
            sources = self.find_prompt_sources(save_node)
            results["connections"][save_node] = {
                "prompt_sources": list(sources.keys()),
                "num_sources": len(sources),
                "shortest_paths": {}
            }
            
            # Find shortest paths
            for prompt_node, paths in sources.items():
                if paths:
                    shortest = min(paths, key=len)
                    results["connections"][save_node]["shortest_paths"][prompt_node] = shortest
            
            # Identify potential issues
            if len(sources) == 0:
                results["issues"].append({
                    "type": "orphan_save",
                    "node": save_node,
                    "message": f"SaveImage node {save_node} has no PromptManager sources"
                })
            elif len(sources) > 1:
                results["issues"].append({
                    "type": "multiple_sources",
                    "node": save_node,
                    "sources": list(sources.keys()),
                    "message": f"SaveImage node {save_node} has {len(sources)} prompt sources"
                })
        
        # Check for orphan PromptManagers
        for prompt_node in self.prompt_managers:
            has_save = False
            for save_node in self.save_nodes:
                if self.find_paths(prompt_node, save_node):
                    has_save = True
                    break
            
            if not has_save:
                results["issues"].append({
                    "type": "orphan_prompt",
                    "node": prompt_node,
                    "message": f"PromptManager {prompt_node} doesn't connect to any SaveImage"
                })
        
        # Calculate complexity score
        total_edges = sum(len(targets) for targets in self.edges.values())
        if self.nodes:
            results["complexity_score"] = total_edges / len(self.nodes)
        
        return results
    
    def get_execution_order(self) -> List[str]:
        """Get topological sort of nodes for execution order.
        
        Returns:
            List of node IDs in execution order
        """
        # Kahn's algorithm for topological sort
        in_degree = defaultdict(int)
        
        for node in self.nodes:
            in_degree[node] = len(self.reverse_edges.get(node, set()))
        
        queue = deque([node for node in self.nodes if in_degree[node] == 0])
        order = []
        
        while queue:
            node = queue.popleft()
            order.append(node)
            
            for neighbor in self.edges.get(node, []):
                in_degree[neighbor] -= 1
                if in_degree[neighbor] == 0:
                    queue.append(neighbor)
        
        if len(order) != len(self.nodes):
            logger.warning("Workflow contains cycles, execution order may be incorrect")
        
        return order
    
    def find_merge_points(self) -> List[str]:
        """Find nodes where multiple paths merge.
        
        Returns:
            List of node IDs that are merge points
        """
        merge_points = []
        
        for node in self.nodes:
            incoming = self.reverse_edges.get(node, set())
            if len(incoming) > 1:
                merge_points.append(node)
        
        return merge_points
    
    def clear(self) -> None:
        """Clear the graph data."""
        self.nodes.clear()
        self.edges.clear()
        self.reverse_edges.clear()
        self.node_types.clear()
        self.prompt_managers.clear()
        self.save_nodes.clear()
    
    def visualize_ascii(self) -> str:
        """Create ASCII visualization of the graph.
        
        Returns:
            ASCII art representation of the workflow
        """
        lines = ["Workflow Graph:"]
        lines.append("=" * 50)
        
        # Show PromptManagers
        lines.append("PromptManagers:")
        for pm in sorted(self.prompt_managers):
            node_type = self.node_types.get(pm, "Unknown")
            lines.append(f"  [{pm}] {node_type}")
        
        lines.append("")
        lines.append("SaveNodes:")
        for sn in sorted(self.save_nodes):
            node_type = self.node_types.get(sn, "Unknown")
            lines.append(f"  [{sn}] {node_type}")
        
        lines.append("")
        lines.append("Connections:")
        
        # Show connections for each save node
        for save_node in sorted(self.save_nodes):
            sources = self.find_prompt_sources(save_node)
            if sources:
                lines.append(f"  SaveNode [{save_node}] <- ")
                for prompt_node, paths in sources.items():
                    shortest = min(paths, key=len) if paths else []
                    path_str = " -> ".join(shortest)
                    lines.append(f"    {path_str}")
        
        return "\n".join(lines)
