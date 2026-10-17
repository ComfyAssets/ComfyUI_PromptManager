"""Paired positive and negative conditioning with reusable recipe storage."""

import hashlib
import json
import time
from typing import Any, Dict, Optional, Tuple

try:
    from .prompt_manager import IO, InputTypeDict, PromptManagerFocused
    from .utils.hashing import generate_prompt_hash
    from .utils.prompt_graph import intrinsic_pair_nodes, run_prompt_nodes
    from .utils.prompt_pair_resolver import resolve_text_input
    from .utils.usage_tracking import (
        PENDING_FIRST_USE,
        PENDING_PAIR_FIRST_USE,
        pair_run_key,
    )
except ImportError:
    from prompt_manager import IO, InputTypeDict, PromptManagerFocused
    from utils.hashing import generate_prompt_hash
    from utils.prompt_graph import intrinsic_pair_nodes, run_prompt_nodes
    from utils.prompt_pair_resolver import resolve_text_input
    from utils.usage_tracking import (
        PENDING_FIRST_USE,
        PENDING_PAIR_FIRST_USE,
        pair_run_key,
    )


class PromptManagerPair(PromptManagerFocused):
    """Keep raw editors as one recipe while encoding their effective texts."""

    @classmethod
    def INPUT_TYPES(cls) -> InputTypeDict:
        inputs = super().INPUT_TYPES()
        inputs["required"] = {
            "text": inputs["required"]["text"],
            "negative_text": (
                IO.STRING,
                {"default": "", "multiline": True, "dynamicPrompts": True},
            ),
            "clip": inputs["required"]["clip"],
        }
        return inputs

    RETURN_TYPES = (IO.CONDITIONING, IO.CONDITIONING, IO.STRING, IO.STRING)
    RETURN_NAMES = ("positive", "negative", "positive_text", "negative_text")
    OUTPUT_TOOLTIPS = (
        "Positive conditioning with composition and optional LoRA trigger words.",
        "Negative conditioning from the negative editor, including empty text.",
        "The effective positive text that was encoded.",
        "The unchanged negative text that was encoded.",
    )
    DESCRIPTION = "Encode and save a reusable positive/negative prompt pair."

    def encode_prompt(
        self,
        clip: Any,
        text: str,
        negative_text: str = "",
        category: str = "",
        tags: str = "",
        search_text: str = "",
        prepend_text: str = "",
        append_text: str = "",
        prompt: Optional[Dict[str, Any]] = None,
        unique_id: Optional[str] = None,
    ) -> Tuple[Any, Any, str, str]:
        """Encode both sides once; database and tracking failures never block CLIP."""
        if clip is None:
            raise RuntimeError("ERROR: clip input is invalid: None")
        if not isinstance(text, str) or not isinstance(negative_text, str):
            raise ValueError("Positive and negative prompt text must be strings")
        positive = self._inject_lora_trigger_words(
            " ".join(
                part
                for part in (prepend_text.strip(), text, append_text.strip())
                if part
            )
        )
        positive_conditioning = clip.encode_from_tokens_scheduled(
            clip.tokenize(positive)
        )
        negative_conditioning = clip.encode_from_tokens_scheduled(
            clip.tokenize(negative_text)
        )
        pair_id = prompt_id = None
        known = isinstance(prompt, dict) and unique_id is not None
        node_id = str(unique_id)
        is_positive = node_id in run_prompt_nodes(prompt) if known else True
        pair_used = node_id in intrinsic_pair_nodes(prompt) if known else True
        try:
            pair_id = self.db.save_prompt_pair(
                text,
                negative_text,
                category=category.strip() or None,
                tags=self._parse_tags(tags),
            )
            pair = self.db.get_prompt_pair(pair_id)
            prompt_id = pair["positive_prompt_id"]
            resolved_positive = (
                resolve_text_input(prompt, node_id, "text") if known else None
            )
            resolved_negative = (
                resolve_text_input(prompt, node_id, "negative_text") if known else None
            )
            positive_seen = resolved_positive is not None and generate_prompt_hash(
                resolved_positive
            ) == generate_prompt_hash(text)
            pair_seen = (
                positive_seen
                and resolved_negative is not None
                and generate_prompt_hash(resolved_negative)
                == generate_prompt_hash(negative_text)
            )
            positive_pending = getattr(self, "pending_first_use", PENDING_FIRST_USE)
            pair_pending = getattr(
                self, "pending_pair_first_use", PENDING_PAIR_FIRST_USE
            )
            if is_positive:
                runs = positive_pending.consume(generate_prompt_hash(text)) + int(
                    not positive_seen
                )
                if runs:
                    self.db.record_prompt_use(prompt_id, times=runs)
            if pair_used:
                runs = pair_pending.consume(pair_run_key(text, negative_text)) + int(
                    not pair_seen
                )
                if runs:
                    self.db.record_prompt_pair_use(pair_id, times=runs)
            if is_positive:
                self.prompt_tracker.set_current_prompt(
                    prompt_text=positive,
                    additional_data={
                        "prompt_id": prompt_id,
                        "pair_id": pair_id if pair_used else None,
                        "positive_text": positive,
                        "negative_text": negative_text,
                        "positive_storage_text": text.strip(),
                        "negative_storage_text": negative_text.strip(),
                        "prepend_text": prepend_text.strip(),
                        "append_text": append_text.strip(),
                        "final_text": positive,
                        "category": category.strip() or None,
                        "tags": self._parse_tags(tags) or [],
                    },
                    push_to_queue=not positive_seen or (pair_used and not pair_seen),
                )
        except Exception as error:
            self.logger.warning(f"Could not save or track paired prompt: {error}")
        try:
            self.comfyui_integration.register_prompt(
                f"promptmanager_pair_{time.time_ns()}",
                positive,
                {
                    "prompt_id": prompt_id,
                    "pair_id": pair_id if pair_used else None,
                    "negative_text": negative_text,
                    "category": category.strip() or None,
                },
            )
        except Exception as error:
            self.logger.warning(f"Could not register paired metadata: {error}")
        return positive_conditioning, negative_conditioning, positive, negative_text

    @classmethod
    def IS_CHANGED(
        cls,
        clip: Any,
        text: str = "",
        negative_text: str = "",
        category: str = "",
        tags: str = "",
        search_text: str = "",
        prepend_text: str = "",
        append_text: str = "",
        **kwargs: Any,
    ) -> str:
        """Either editor, composition or pair metadata invalidates the node cache."""
        serialized = json.dumps(
            [text, negative_text, prepend_text, append_text, category, tags]
        )
        return hashlib.sha256(serialized.encode("utf-8")).hexdigest()
