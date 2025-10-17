"""Server-side statistics aggregation for PromptManager."""

from __future__ import annotations

import json
import math
import sqlite3
from ..database.connection_helper import DatabaseConnection
import time
from collections import Counter, defaultdict
from copy import deepcopy
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional


class StatsService:
    """Aggregate prompt/image analytics without loading millions of rows in the client."""

    def __init__(self, db_path: str | Path, *, cache_ttl: float = 300.0) -> None:
        self.db_path = str(db_path)
        self.cache_ttl = cache_ttl
        self._cached_snapshot: Optional[Dict[str, Any]] = None
        self._cached_at: float = 0.0

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def get_overview(self, *, force: bool = False) -> Dict[str, Any]:
        """Return an aggregated statistics snapshot.

        Args:
            force: When True skip cache reuse and rebuild statistics.
        """
        now = time.time()
        if (
            not force
            and self._cached_snapshot is not None
            and now - self._cached_at < self.cache_ttl
        ):
            return deepcopy(self._cached_snapshot)

        prompts = self._fetch_prompts()
        images = self._fetch_images()
        tracking = self._fetch_tracking()

        snapshot = self._build_snapshot(prompts, images, tracking)
        snapshot["generatedAt"] = datetime.utcnow().isoformat() + "Z"

        self._cached_snapshot = deepcopy(snapshot)
        self._cached_at = now
        return snapshot

    # ------------------------------------------------------------------
    # Data loading helpers
    # ------------------------------------------------------------------
    def _connect(self) -> sqlite3.Connection:
        conn = DatabaseConnection.get_connection(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _fetch_prompts(self) -> List[Dict[str, Any]]:
        query = (
            "SELECT id, positive_prompt, negative_prompt, category, tags, rating, notes, "
            "hash, model_hash, sampler_settings, generation_params, created_at "
            "FROM prompts"
        )
        with self._connect() as conn:
            rows = conn.execute(query).fetchall()

        prompts: List[Dict[str, Any]] = []
        for row in rows:
            data = dict(row)
            data["created_at"] = self._parse_datetime(data.get("created_at"))
            data["positive_prompt"] = data.get("positive_prompt") or data.get("prompt") or ""
            data["negative_prompt"] = data.get("negative_prompt") or ""
            data["category"] = data.get("category") or None
            data["tags"] = self._coerce_json(data.get("tags"), default=[])
            data["rating"] = self._safe_int(data.get("rating"))
            data["sampler_settings"] = self._coerce_json(data.get("sampler_settings"), default={})
            data["generation_params"] = self._coerce_json(data.get("generation_params"), default={})
            prompts.append(data)
        return prompts

    def _fetch_images(self) -> List[Dict[str, Any]]:
        with self._connect() as conn:
            # Get available columns
            cursor = conn.execute("PRAGMA table_info(generated_images)")
            available_columns = {row[1] for row in cursor.fetchall()}
            
            # Build query with only available columns
            base_columns = ['id', 'prompt_id']
            optional_columns = ['image_path', 'filename', 'generation_time', 'file_size', 
                              'width', 'height', 'format', 'workflow_data', 
                              'prompt_metadata', 'parameters', 'created_at']
            
            # Add file_path/file_name fallbacks for v1 compatibility
            if 'file_path' in available_columns and 'image_path' not in available_columns:
                optional_columns.append('file_path')
            if 'file_name' in available_columns and 'filename' not in available_columns:
                optional_columns.append('file_name')
            
            selected_columns = base_columns + [col for col in optional_columns if col in available_columns]
            query = f"SELECT {', '.join(selected_columns)} FROM generated_images"
            
            rows = conn.execute(query).fetchall()

        images: List[Dict[str, Any]] = []
        for row in rows:
            data = dict(row)
            
            # Handle column name variations (v1 vs v2 compatibility)
            if 'file_path' in data and 'image_path' not in data:
                data['image_path'] = data['file_path']
            if 'file_name' in data and 'filename' not in data:
                data['filename'] = data['file_name']
            
            # Handle timestamp columns
            generation_time = data.get("generation_time") or data.get("created_at")
            data["generation_time"] = self._parse_datetime(generation_time)
            data["created_at"] = data["generation_time"]
            data["file_size"] = self._safe_int(data.get("file_size"))
            data["width"] = self._safe_int(data.get("width"))
            data["height"] = self._safe_int(data.get("height"))
            data["format"] = (data.get("format") or "").lower() or "unknown"
            data["workflow_data"] = self._coerce_json(data.get("workflow_data"), default={})
            data["prompt_metadata"] = self._coerce_json(data.get("prompt_metadata"), default={})
            data["parameters"] = self._coerce_json(data.get("parameters"), default={})
            data.setdefault("media_type", "image")
            data.setdefault("thumbnail_small_path", None)
            data.setdefault("thumbnail_medium_path", None)
            images.append(data)
        return images

    def _fetch_tracking(self) -> List[Dict[str, Any]]:
        query = "SELECT session_id, prompt_text, created_at FROM prompt_tracking"
        with self._connect() as conn:
            rows = conn.execute(query).fetchall()

        tracking: List[Dict[str, Any]] = []
        for row in rows:
            created = self._parse_datetime(row["created_at"])
            tracking.append(
                {
                    "session_id": row["session_id"],
                    "prompt_text": row["prompt_text"],
                    "created_at": created,
                }
            )
        return tracking

    # ------------------------------------------------------------------
    # Snapshot builder
    # ------------------------------------------------------------------
    def _build_snapshot(
        self,
        prompts: List[Dict[str, Any]],
        images: List[Dict[str, Any]],
        tracking: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        total_sessions = len({t["session_id"] for t in tracking})

        snapshot: Dict[str, Any] = {
            "totalPrompts": len(prompts),
            "totalImages": len(images),
            "totalSessions": total_sessions,
        }

        snapshot["totals"] = {
            "prompts": len(prompts),
            "images": len(images),
            "sessions": total_sessions,
        }

        snapshot["timeAnalytics"] = self._calculate_time_analytics(prompts, images)
        snapshot["promptPatterns"] = self._analyze_prompt_patterns(prompts)
        snapshot["generationMetrics"] = self._analyze_generation_metrics(images)
        snapshot["userBehavior"] = self._analyze_user_behavior(tracking, prompts)
        snapshot["modelPerformance"] = self._analyze_model_performance(prompts, images)
        snapshot["qualityMetrics"] = self._analyze_quality_metrics(prompts, images)
        snapshot["workflowAnalysis"] = self._analyze_workflows(images, tracking)
        snapshot["trends"] = self._analyze_trends(prompts, images)

        return snapshot

    # ------------------------------------------------------------------
    # Analytics helpers (ported from frontend logic)
    # ------------------------------------------------------------------
    def _calculate_time_analytics(
        self,
        prompts: List[Dict[str, Any]],
        images: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        hourly_activity = [0] * 24
        weekday_activity = [0] * 7
        monthly_growth: Dict[str, int] = Counter()

        image_dates: List[datetime] = []
        for image in images:
            dt = image.get("generation_time") or image.get("created_at")
            dt = self._ensure_datetime(dt)
            if not dt:
                continue
            image_dates.append(dt)
            hour = dt.hour
            weekday = (dt.weekday() + 1) % 7  # Align with JS Date.getDay()
            hourly_activity[hour] += 1
            weekday_activity[weekday] += 1
            monthly_growth[dt.strftime("%Y-%m")] += 1

        peak_hours: List[Dict[str, Any]] = []
        if image_dates:
            max_count = max(hourly_activity)
            if max_count > 0:
                for hour, count in enumerate(hourly_activity):
                    if count > max_count * 0.7:
                        percentage = self._safe_fixed(
                            self._safe_percentage(count, max_count) * 100.0,
                            1,
                        )
                        peak_hours.append(
                            {
                                "hour": hour,
                                "count": count,
                                "percentage": float(percentage),
                            }
                        )
                peak_hours.sort(key=lambda item: item["count"], reverse=True)

        date_set = {dt.date() for dt in image_dates}
        today = datetime.utcnow().date()
        current_streak = 0
        max_streak = 0

        for offset in range(365):
            day = today - timedelta(days=offset)
            if day in date_set:
                current_streak += 1
                max_streak = max(max_streak, current_streak)
            elif offset < 7:
                continue
            else:
                break

        return {
            "hourlyActivity": hourly_activity,
            "weekdayActivity": weekday_activity,
            "monthlyGrowth": dict(sorted(monthly_growth.items())),
            "generationSpeed": [],
            "peakHours": peak_hours,
            "longestStreak": max_streak,
            "currentStreak": current_streak,
        }

    def _analyze_prompt_patterns(self, prompts: List[Dict[str, Any]]) -> Dict[str, Any]:
        patterns = {
            "topWords": {},
            "stylePatterns": {},
            "negativePatterns": {},
            "complexityDistribution": {
                "simple": 0,
                "moderate": 0,
                "complex": 0,
                "extreme": 0,
            },
            "avgPromptLength": 0.0,
            "vocabularySize": 0,
            "mostCreative": [],
            "evolution": [],
            "themes": {},
            "ignoredWords": [],
        }

        if not prompts:
            return patterns

        all_words: set[str] = set()
        word_freq: Counter[str] = Counter()

        for prompt in prompts:
            text = (prompt.get("positive_prompt") or "").lower()
            words = [word for word in self._split_words(text) if len(word) > 3]
            for word in words:
                word_freq[word] += 1
                all_words.add(word)

            complexity = self._calculate_complexity(prompt.get("positive_prompt"))
            if complexity < 50:
                patterns["complexityDistribution"]["simple"] += 1
            elif complexity < 100:
                patterns["complexityDistribution"]["moderate"] += 1
            elif complexity < 200:
                patterns["complexityDistribution"]["complex"] += 1
            else:
                patterns["complexityDistribution"]["extreme"] += 1

            for style in self._extract_styles(prompt.get("positive_prompt")):
                patterns["stylePatterns"][style] = patterns["stylePatterns"].get(style, 0) + 1

            negative_text = (prompt.get("negative_prompt") or "").lower()
            if negative_text:
                for token in self._split_words(negative_text):
                    if len(token) > 2:
                        patterns["negativePatterns"][token] = (
                            patterns["negativePatterns"].get(token, 0) + 1
                        )

        patterns["topWords"] = dict(
            sorted(word_freq.items(), key=lambda item: item[1], reverse=True)[:50]
        )
        patterns["vocabularySize"] = len(all_words)
        avg_length = self._safe_divide(
            sum(len(prompt.get("positive_prompt") or "") for prompt in prompts),
            len(prompts),
        )
        patterns["avgPromptLength"] = avg_length

        most_creative = []
        for prompt in prompts:
            uniqueness = self._calculate_uniqueness(
                prompt.get("positive_prompt"), prompts
            )
            most_creative.append(
                {
                    "id": prompt.get("id"),
                    "positive_prompt": prompt.get("positive_prompt"),
                    "category": prompt.get("category"),
                    "uniqueness": self._safe_fixed(uniqueness, 4),
                }
            )
        most_creative.sort(key=lambda item: float(item["uniqueness"]), reverse=True)
        patterns["mostCreative"] = most_creative[:10]
        return patterns

    def _analyze_generation_metrics(self, images: List[Dict[str, Any]]) -> Dict[str, Any]:
        metrics = {
            "resolutions": {},
            "avgFileSize": 0.0,
            "totalDiskUsage": 0,
            "formats": {},
            "aspectRatios": {
                "portrait": 0,
                "landscape": 0,
                "square": 0,
                "ultrawide": 0,
            },
            "avgGenerationTime": 0.0,
            "qualityTiers": {"low": 0, "medium": 0, "high": 0, "ultra": 0},
            "mediaTypes": {"image": 0, "video": 0, "gif": 0},
            "thumbnailCoverage": 0.0,
        }

        if not images:
            return metrics

        total_size = 0
        thumbnail_count = 0

        for image in images:
            width = image.get("width")
            height = image.get("height")
            if width and height:
                res_key = f"{width}x{height}"
                metrics["resolutions"][res_key] = metrics["resolutions"].get(res_key, 0) + 1
                ratio = width / height if height else 0
                if ratio < 0.8:
                    metrics["aspectRatios"]["portrait"] += 1
                elif ratio > 1.2 and ratio < 2.0:
                    metrics["aspectRatios"]["landscape"] += 1
                elif 0.8 <= ratio <= 1.2:
                    metrics["aspectRatios"]["square"] += 1
                elif ratio >= 2.0:
                    metrics["aspectRatios"]["ultrawide"] += 1

            file_size = image.get("file_size") or 0
            total_size += file_size
            size_mb = file_size / (1024 * 1024)
            if size_mb < 1:
                metrics["qualityTiers"]["low"] += 1
            elif size_mb < 3:
                metrics["qualityTiers"]["medium"] += 1
            elif size_mb < 10:
                metrics["qualityTiers"]["high"] += 1
            else:
                metrics["qualityTiers"]["ultra"] += 1

            fmt = (image.get("format") or "unknown").lower()
            metrics["formats"][fmt] = metrics["formats"].get(fmt, 0) + 1

            media_type = (image.get("media_type") or "image").lower()
            metrics["mediaTypes"][media_type] = metrics["mediaTypes"].get(media_type, 0) + 1

            if image.get("thumbnail_small_path") or image.get("thumbnail_medium_path"):
                thumbnail_count += 1

        metrics["avgFileSize"] = self._safe_divide(total_size, len(images))
        metrics["totalDiskUsage"] = total_size
        metrics["thumbnailCoverage"] = self._safe_percentage(thumbnail_count, len(images), 1)

        return metrics

    def _analyze_user_behavior(
        self,
        tracking: List[Dict[str, Any]],
        prompts: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        behavior = {
            "avgSessionLength": 0.0,
            "sessionsPerDay": {},
            "refinementRate": 0.0,
            "avgRevisionsPerPrompt": 0.0,
            "categoryDistribution": {},
            "ratingDistribution": {str(i): 0 for i in range(1, 6)},
            "popularTags": {},
            "workflowComplexity": {
                "simple": 0,
                "moderate": 0,
                "complex": 0,
            },
            "experimentationScore": 0.0,
        }

        sessions: Dict[str, Dict[str, Any]] = {}
        for entry in tracking:
            session_id = entry.get("session_id") or "unknown"
            created_at = self._ensure_datetime(entry.get("created_at"))
            if not created_at:
                continue
            if session_id not in sessions:
                sessions[session_id] = {
                    "start": created_at,
                    "end": created_at,
                    "prompts": [entry.get("prompt_text")],
                }
            else:
                sessions[session_id]["end"] = created_at
                sessions[session_id]["prompts"].append(entry.get("prompt_text"))

        session_lengths = [
            max(0.0, (session["end"] - session["start"]).total_seconds() / 60.0)
            for session in sessions.values()
        ]
        behavior["avgSessionLength"] = self._safe_divide(sum(session_lengths), len(session_lengths))

        sessions_per_day: Counter[str] = Counter()
        for session in sessions.values():
            day = session["start"].date().isoformat()
            sessions_per_day[day] += 1
        behavior["sessionsPerDay"] = dict(sorted(sessions_per_day.items()))

        if sessions:
            revisions = [len(session["prompts"]) for session in sessions.values()]
            behavior["avgRevisionsPerPrompt"] = self._safe_divide(sum(revisions), len(revisions))

        for prompt in prompts:
            category = prompt.get("category")
            if category:
                behavior["categoryDistribution"][category] = (
                    behavior["categoryDistribution"].get(category, 0) + 1
                )
            rating_value = self._safe_int(prompt.get("rating"))
            if rating_value is not None:
                key = str(rating_value)
                behavior["ratingDistribution"][key] = (
                    behavior["ratingDistribution"].get(key, 0) + 1
                )
            for tag in self._normalize_tags(prompt.get("tags")):
                behavior["popularTags"][tag] = behavior["popularTags"].get(tag, 0) + 1

        unique_prompts = len({prompt.get("positive_prompt") for prompt in prompts if prompt.get("positive_prompt")})
        behavior["experimentationScore"] = self._safe_percentage(unique_prompts, len(prompts), 1)

        return behavior

    def _analyze_model_performance(
        self,
        prompts: List[Dict[str, Any]],
        images: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        performance = {
            "modelUsage": {},
            "samplerPreferences": {},
            "modelSuccessRate": {},
            "popularSettings": [],
            "modelSwitchingRate": 0.0,
            "optimalConfigs": [],
        }

        for prompt in prompts:
            model_hash = prompt.get("model_hash")
            if model_hash:
                performance["modelUsage"][model_hash] = performance["modelUsage"].get(model_hash, 0) + 1
                rating = prompt.get("rating")
                if rating is not None and rating >= 4:
                    bucket = performance["modelSuccessRate"].setdefault(model_hash, {"success": 0, "total": 0})
                    bucket["success"] += 1
                    bucket["total"] += 1
                elif rating is not None:
                    bucket = performance["modelSuccessRate"].setdefault(model_hash, {"success": 0, "total": 0})
                    bucket["total"] += 1

            sampler_settings = prompt.get("sampler_settings") or {}
            if isinstance(sampler_settings, dict):
                sampler = sampler_settings.get("sampler", "unknown")
                steps = sampler_settings.get("steps", 0)
                key = f"{sampler}_{steps}"
                performance["samplerPreferences"][key] = (
                    performance["samplerPreferences"].get(key, 0) + 1
                )

        performance["popularSettings"] = [
            {"setting": key, "count": count}
            for key, count in sorted(
                performance["samplerPreferences"].items(), key=lambda item: item[1], reverse=True
            )[:10]
        ]

        performance["optimalConfigs"] = [
            {
                "model": prompt.get("model_hash"),
                "params": prompt.get("generation_params"),
                "prompt_excerpt": (prompt.get("positive_prompt") or "")[:50],
            }
            for prompt in prompts
            if prompt.get("rating") == 5 and prompt.get("generation_params")
        ][:5]

        return performance

    def _analyze_quality_metrics(
        self,
        prompts: List[Dict[str, Any]],
        images: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        quality = {
            "ratingTrend": [],
            "improvementRate": 0.0,
            "topCategories": [],
            "qualityByHour": [0] * 24,
            "consistencyScore": 0.0,
            "innovationIndex": 0.0,
        }

        rated_prompts = [
            prompt for prompt in prompts if prompt.get("created_at") and prompt.get("rating") is not None
        ]
        rated_prompts.sort(key=lambda prompt: self._ensure_datetime(prompt.get("created_at")) or datetime.utcnow())

        monthly_ratings: Dict[str, Dict[str, float]] = {}
        for prompt in rated_prompts:
            created_at = self._ensure_datetime(prompt.get("created_at"))
            if not created_at:
                continue
            key = created_at.strftime("%Y-%m")
            entry = monthly_ratings.setdefault(key, {"sum": 0.0, "count": 0})
            entry["sum"] += prompt.get("rating") or 0
            entry["count"] += 1

        for month, data in sorted(monthly_ratings.items()):
            avg = self._safe_divide(data["sum"], data["count"])
            quality["ratingTrend"].append({"month": month, "avgRating": self._safe_fixed(avg, 2)})

        if len(quality["ratingTrend"]) >= 2:
            first = float(quality["ratingTrend"][0]["avgRating"])
            last = float(quality["ratingTrend"][-1]["avgRating"])
            if first != 0:
                improvement = ((last - first) / first) * 100
            else:
                improvement = last * 100
            quality["improvementRate"] = self._safe_fixed(improvement, 1)

        category_ratings: Dict[str, Dict[str, float]] = {}
        for prompt in prompts:
            category = prompt.get("category")
            rating = prompt.get("rating")
            if category and rating is not None:
                entry = category_ratings.setdefault(category, {"sum": 0.0, "count": 0})
                entry["sum"] += rating
                entry["count"] += 1

        quality["topCategories"] = [
            {
                "category": category,
                "avgRating": self._safe_fixed(self._safe_divide(data["sum"], data["count"]), 2),
                "count": data["count"],
            }
            for category, data in sorted(
                category_ratings.items(),
                key=lambda item: self._safe_divide(item[1]["sum"], item[1]["count"]),
                reverse=True,
            )[:10]
        ]

        ratings = [prompt.get("rating") for prompt in prompts if prompt.get("rating") is not None]
        if ratings:
            avg_rating = self._safe_divide(sum(ratings), len(ratings))
            variance = self._safe_divide(
                sum((rating - avg_rating) ** 2 for rating in ratings),
                len(ratings),
            )
            quality["consistencyScore"] = self._safe_fixed(max(0.0, 100.0 - variance * 20.0), 1)

        unique_words = set()
        for prompt in prompts:
            for word in self._split_words(prompt.get("positive_prompt") or ""):
                unique_words.add(word)
        quality["innovationIndex"] = self._safe_fixed(min(100.0, len(unique_words) / 10.0), 1)

        return quality

    def _analyze_workflows(
        self,
        images: List[Dict[str, Any]],
        tracking: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        workflows = {
            "nodeUsage": {},
            "complexityLevels": {
                "simple": 0,
                "moderate": 0,
                "complex": 0,
                "advanced": 0,
            },
            "commonPatterns": [],
            "complexityTrend": [],
            "uniqueWorkflowCount": 0,
        }

        workflow_hashes: set[str] = set()
        for image in images:
            workflow_data = image.get("workflow_data") or {}
            if not workflow_data:
                continue

            wf_json = json.dumps(workflow_data, sort_keys=True)
            workflow_hashes.add(wf_json)

            nodes = workflow_data.get("nodes") or {}
            if isinstance(nodes, list):
                nodes = {
                    str(index): node
                    for index, node in enumerate(nodes)
                    if isinstance(node, dict)
                }
            elif not isinstance(nodes, dict):
                nodes = {}
            node_count = len(nodes)
            if node_count < 5:
                workflows["complexityLevels"]["simple"] += 1
            elif node_count < 10:
                workflows["complexityLevels"]["moderate"] += 1
            elif node_count < 20:
                workflows["complexityLevels"]["complex"] += 1
            else:
                workflows["complexityLevels"]["advanced"] += 1

            for node in nodes.values():
                if not isinstance(node, dict):
                    continue
                node_type = node.get("class_type") or node.get("type") or "unknown"
                workflows["nodeUsage"][node_type] = workflows["nodeUsage"].get(node_type, 0) + 1

        workflows["uniqueWorkflowCount"] = len(workflow_hashes)
        return workflows

    def _analyze_trends(
        self,
        prompts: List[Dict[str, Any]],
        images: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        trends = {
            "projectedGrowth": {},
            "trendingStyles": [],
            "emergingPatterns": [],
            "seasonalTrends": {},
            "predictedPeakTimes": [],
            "velocityScore": self._safe_fixed(0.0, 1),
        }

        images_by_month: Dict[str, int] = Counter()
        image_records: List[datetime] = []
        for image in images:
            dt = self._ensure_datetime(image.get("generation_time") or image.get("created_at"))
            if not dt:
                continue
            image_records.append(dt)
            images_by_month[dt.strftime("%Y-%m")] += 1

        months = sorted(images_by_month.keys())
        if len(months) >= 3:
            recent_months = months[-3:]
            recent_counts = [images_by_month[m] for m in recent_months]
            avg_growth = self._safe_divide((recent_counts[-1] - recent_counts[0]), 2)
            last_count = recent_counts[-1]
            for i in range(1, 4):
                trends["projectedGrowth"][f"Month +{i}"] = max(0, round(last_count + avg_growth * i))

        recent_days = 7
        cutoff = datetime.utcnow() - timedelta(days=recent_days)
        recent_images = [dt for dt in image_records if dt >= cutoff]
        weekly_rate = self._safe_divide(len(recent_images), recent_days) * 7.0
        trends["velocityScore"] = self._safe_fixed(weekly_rate, 1)

        overall_styles: Counter[str] = Counter()
        recent_styles: Counter[str] = Counter()
        for prompt in prompts:
            created = self._ensure_datetime(prompt.get("created_at"))
            if not created:
                continue
            styles = self._extract_styles(prompt.get("positive_prompt"))
            is_recent = created >= cutoff
            for style in styles:
                overall_styles[style] += 1
                if is_recent:
                    recent_styles[style] += 1

        trending = []
        for style, recent_count in recent_styles.items():
            overall_total = overall_styles.get(style, 0)
            overall_avg = self._safe_divide(overall_total, 30)
            recent_avg = self._safe_divide(recent_count, recent_days)
            ratio = recent_avg / overall_avg if overall_avg > 0 else recent_avg
            rising = recent_avg > overall_avg
            if rising:
                trending.append(
                    {
                        "style": style,
                        "trendScore": self._safe_fixed(ratio, 2),
                        "recentCount": recent_count,
                        "rising": True,
                    }
                )
        trending.sort(key=lambda item: float(item["trendScore"]), reverse=True)
        trends["trendingStyles"] = trending[:10]

        return trends

    # ------------------------------------------------------------------
    # Utility helpers
    # ------------------------------------------------------------------
    @staticmethod
    def _safe_int(value: Any) -> Optional[int]:
        if value in (None, ""):
            return None
        try:
            return int(value)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _coerce_json(value: Any, default: Any) -> Any:
        if isinstance(value, (dict, list)):
            return value
        if value in (None, ""):
            return default
        if isinstance(value, str):
            try:
                return json.loads(value)
            except json.JSONDecodeError:
                return default
        return default

    @staticmethod
    def _parse_datetime(value: Any) -> Optional[str]:
        if not value:
            return None
        dt = StatsService._ensure_datetime(value)
        if not dt:
            return None
        return dt.strftime("%Y-%m-%d %H:%M:%S")

    @staticmethod
    def _ensure_datetime(value: Any) -> Optional[datetime]:
        if isinstance(value, datetime):
            return value
        if isinstance(value, (int, float)):
            return datetime.utcfromtimestamp(value)
        if isinstance(value, str):
            text = value.strip()
            if not text:
                return None
            text = text.replace("Z", "+00:00")
            try:
                return datetime.fromisoformat(text)
            except ValueError:
                for fmt in (
                    "%Y-%m-%d %H:%M:%S",
                    "%Y-%m-%dT%H:%M:%S",
                    "%Y-%m-%d %H:%M:%S.%f",
                    "%Y-%m-%dT%H:%M:%S.%f",
                ):
                    try:
                        return datetime.strptime(text, fmt)
                    except ValueError:
                        continue
        return None

    @staticmethod
    def _safe_divide(numerator: float, denominator: float) -> float:
        if not denominator:
            return 0.0
        try:
            result = numerator / denominator
        except ZeroDivisionError:
            return 0.0
        if math.isnan(result) or math.isinf(result):
            return 0.0
        return result

    @staticmethod
    def _safe_percentage(value: float, total: float, precision: int = 1) -> float:
        result = StatsService._safe_divide(value, total) * 100.0
        return round(result, precision)

    @staticmethod
    def _safe_fixed(value: float, precision: int = 1) -> str:
        if math.isnan(value) or math.isinf(value):
            value = 0.0
        return f"{value:.{precision}f}"

    @staticmethod
    def _split_words(text: str) -> List[str]:
        if not text:
            return []
        tokens = [token for token in re_split_pattern.split(text) if token]
        return tokens

    @staticmethod
    def _normalize_tags(value: Any) -> List[str]:
        if value is None:
            return []
        if isinstance(value, list):
            return [str(item).strip() for item in value if str(item).strip()]
        if isinstance(value, str):
            text = value.strip()
            if not text:
                return []
            if (text.startswith("[") and text.endswith("]")) or (
                text.startswith("{") and text.endswith("}")
            ):
                try:
                    parsed = json.loads(text)
                    return StatsService._normalize_tags(parsed)
                except json.JSONDecodeError:
                    pass
            return [token.strip() for token in text.split(",") if token.strip()]
        if isinstance(value, dict):
            if "tags" in value:
                return StatsService._normalize_tags(value["tags"])
            if "items" in value:
                return StatsService._normalize_tags(value["items"])
            return StatsService._normalize_tags(list(value.values()))
        return [str(value).strip()]

    @staticmethod
    def _calculate_complexity(prompt: Optional[str]) -> float:
        if not prompt:
            return 0.0
        prompt = str(prompt)
        length_score = len(prompt) / 10.0
        commas = prompt.count(",") * 2.0
        parentheses = sum(prompt.count(ch) for ch in "()") * 3.0
        weights = sum(1 for _ in WEIGHT_PATTERN.finditer(prompt)) * 5.0
        return length_score + commas + parentheses + weights

    @staticmethod
    def _calculate_uniqueness(prompt: Optional[str], all_prompts: Iterable[Dict[str, Any]]) -> float:
        if not prompt:
            return 0.0
        words = {token for token in StatsService._split_words(prompt.lower()) if token}
        if not words:
            return 0.0
        uniqueness = 0.0
        for word in words:
            occurrences = 0
            for other in all_prompts:
                other_text = (other.get("positive_prompt") or "").lower()
                if word in other_text:
                    occurrences += 1
            if occurrences:
                uniqueness += 1 / occurrences
        return uniqueness

    @staticmethod
    def _extract_styles(prompt: Optional[str]) -> List[str]:
        if not prompt:
            return []
        lower = prompt.lower()
        found: List[str] = []
        for style in STYLE_KEYWORDS:
            if style in lower:
                found.append(style)
        return found


# Precompiled regex for word splitting and weight detection
import re

re_split_pattern = re.compile(r"[\s,;.]+")
WEIGHT_PATTERN = re.compile(r":\d+(\.\d+)?")
STYLE_KEYWORDS = [
    'realistic', 'anime', 'cartoon', 'photorealistic', 'digital art',
    'oil painting', 'watercolor', 'sketch', 'concept art', 'fantasy',
    'sci-fi', 'cyberpunk', 'steampunk', 'gothic', 'minimalist',
    'abstract', 'surreal', 'impressionist', 'baroque', 'renaissance'
]
