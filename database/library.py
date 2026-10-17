"""Read-only positive-parent views over existing prompt and pair tables."""

_ELIGIBLE = """(EXISTS(SELECT 1 FROM prompt_pairs ep WHERE ep.positive_prompt_id=p.id)
 OR EXISTS(SELECT 1 FROM generated_images eg WHERE eg.prompt_id=p.id)
 OR NOT EXISTS(SELECT 1 FROM prompt_pairs en WHERE en.negative_prompt_id=p.id))"""
_ORDERS = {
    "newest": ("COALESCE(julianday(p.created_at),0)", "DESC"),
    "oldest": ("COALESCE(julianday(p.created_at),0)", "ASC"),
    "name": ("LOWER(p.text)", "ASC"),
    "rating": ("COALESCE(p.rating,0)", "DESC"),
}


def _escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _limit(value: int) -> None:
    if type(value) is not int or not 1 <= value <= 200:
        raise ValueError("limit must be between 1 and 200")


def _filter(text: str, category: str | None, tags: list[str]) -> tuple[str, list]:
    if not isinstance(text, str) or len(text) > 500:
        raise ValueError("Search text must be a string of at most 500 characters")
    if category is not None and (not isinstance(category, str) or len(category) > 200):
        raise ValueError("Invalid library category")
    if (
        not isinstance(tags, list)
        or len(tags) > 30
        or any(not isinstance(tag, str) or not tag or len(tag) > 200 for tag in tags)
    ):
        raise ValueError("Invalid library tags")
    parent, pair, parent_params, pair_params = [], ["pp.archived=0"], [], []
    if text:
        parent.append("p.text LIKE ? ESCAPE '\\'")
        parent_params.append(f"%{_escape(text)}%")
        pair.append(
            "(p.text LIKE ? ESCAPE '\\' OR n.text LIKE ? ESCAPE '\\' "
            "OR pp.title LIKE ? ESCAPE '\\')"
        )
        pair_params.extend([f"%{_escape(text)}%"] * 3)
    if category is not None:
        parent.append("p.category=?")
        pair.append("pp.category=?")
        parent_params.append(category)
        pair_params.append(category)
    for tag in tags:
        parent.append(
            "EXISTS(SELECT 1 FROM prompt_tags pt JOIN tags t ON t.id=pt.tag_id "
            "WHERE pt.prompt_id=p.id AND t.name=?)"
        )
        pair.append(
            "EXISTS(SELECT 1 FROM prompt_pair_tags pt JOIN tags t ON t.id=pt.tag_id "
            "WHERE pt.pair_id=pp.id AND t.name=?)"
        )
        parent_params.append(tag)
        pair_params.append(tag)
    condition = (
        "("
        + (" AND ".join(parent) or "1=1")
        + (
            ") OR EXISTS(SELECT 1 FROM prompt_pairs pp "
            "LEFT JOIN prompts n ON n.id=pp.negative_prompt_id "
            "WHERE pp.positive_prompt_id=p.id AND "
        )
        + " AND ".join(pair)
        + ")"
    )
    return f"({_ELIGIBLE}) AND ({condition})", [*parent_params, *pair_params]


class LibraryOperations:
    """Group reusable negative variants without changing legacy prompt queries."""

    def _library_parents(
        self,
        where: str,
        params: list,
        suffix: str = "",
        sort_expression: str | None = None,
    ) -> list[dict]:
        conn = self.model.get_connection()
        rows = conn.execute(
            "SELECT p.id,p.text,p.category,p.rating,p.created_at,"
            + (f"{sort_expression} AS _sort," if sort_expression else "")
            + "(SELECT COUNT(*) FROM prompt_pairs v WHERE v.positive_prompt_id=p.id "
            "AND v.archived=0) AS variant_count,"
            "(SELECT COUNT(*) FROM generated_images g "
            "WHERE g.prompt_id=p.id) AS image_count "
            "FROM prompts p WHERE " + where + suffix,
            params,
        ).fetchall()
        result = [{**dict(row), "tags": []} for row in rows]
        if result:
            by_id = {row["id"]: row for row in result}
            marks = ",".join("?" for _ in result)
            for row in conn.execute(
                "SELECT pt.prompt_id,t.name FROM prompt_tags pt "
                "JOIN tags t ON t.id=pt.tag_id "
                f"WHERE pt.prompt_id IN ({marks}) ORDER BY t.name",
                list(by_id),
            ):
                by_id[row["prompt_id"]]["tags"].append(row["name"])
        return result

    def list_library(
        self,
        *,
        text: str = "",
        category: str | None = None,
        tags: list[str] | None = None,
        sort: str = "newest",
        limit: int = 40,
        after: dict | None = None,
    ) -> dict:
        _limit(limit)
        if sort not in _ORDERS:
            raise ValueError("Unknown library sort order")
        where, params = _filter(text, category, [] if tags is None else tags)
        conn = self.model.get_connection()
        ceiling = conn.execute("SELECT COALESCE(MAX(id),0) FROM prompts").fetchone()[0]
        if after is not None:
            if (
                not isinstance(after, dict)
                or set(after) != {"ceiling", "value", "id"}
                or type(after["ceiling"]) is not int
                or type(after["id"]) is not int
            ):
                raise ValueError("Invalid library cursor")
            ceiling = after["ceiling"]
        where += " AND p.id<=?"
        params.append(ceiling)
        total = conn.execute(
            "SELECT COUNT(*) FROM prompts p WHERE " + where, params
        ).fetchone()[0]
        expression, direction = _ORDERS[sort]
        if after is not None:
            operator = ">" if direction == "ASC" else "<"
            where += (
                f" AND ({expression}{operator}? OR "
                f"({expression}=? AND p.id{operator}?))"
            )
            params.extend([after["value"], after["value"], after["id"]])
        rows = self._library_parents(
            where,
            [*params, limit + 1],
            f" ORDER BY {expression} {direction},p.id {direction} LIMIT ?",
            sort_expression=expression,
        )
        has_more = len(rows) > limit
        rows = rows[:limit]
        next_key = None
        if has_more:
            next_key = {
                "ceiling": ceiling,
                "value": rows[-1]["_sort"],
                "id": rows[-1]["id"],
            }
        for row in rows:
            row.pop("_sort")
        return {
            "items": rows,
            "total": total,
            "has_more": has_more,
            "next_key": next_key,
        }

    def get_library_positive(
        self,
        positive_id: int,
        *,
        variant_limit: int = 40,
        variant_after: int | None = None,
    ) -> dict | None:
        _limit(variant_limit)
        if variant_after is not None and (
            type(variant_after) is not int or variant_after <= 0
        ):
            raise ValueError("Invalid variant cursor")
        rows = self._library_parents(_ELIGIBLE + " AND p.id=?", [positive_id])
        if not rows:
            return None
        conn = self.model.get_connection()
        where = "pp.positive_prompt_id=? AND pp.archived=0"
        params = [positive_id]
        variant_total = conn.execute(
            "SELECT COUNT(*) FROM prompt_pairs pp WHERE " + where, params
        ).fetchone()[0]
        if variant_after is not None:
            where += " AND pp.id<?"
            params.append(variant_after)
        variants = [
            dict(row)
            for row in conn.execute(
                "SELECT pp.id,COALESCE(n.text,'') AS negative_text,"
                "pp.category,pp.title,"
                "pp.negative_prompt_id IS NULL AS explicit_empty,"
                "(SELECT COUNT(DISTINCT image_id) FROM image_prompt_pairs ip "
                "WHERE ip.pair_id=pp.id) AS image_count "
                "FROM prompt_pairs pp "
                "LEFT JOIN prompts n ON n.id=pp.negative_prompt_id "
                "WHERE " + where + " ORDER BY pp.id DESC LIMIT ?",
                [*params, variant_limit + 1],
            )
        ]
        variants_has_more = len(variants) > variant_limit
        variants = variants[:variant_limit]
        by_id = {row["id"]: row for row in variants}
        for row in variants:
            row["tags"] = []
            row["explicit_empty"] = bool(row["explicit_empty"])
        if by_id:
            marks = ",".join("?" for _ in by_id)
            for row in conn.execute(
                "SELECT pt.pair_id,t.name FROM prompt_pair_tags pt "
                "JOIN tags t ON t.id=pt.tag_id "
                f"WHERE pt.pair_id IN ({marks}) ORDER BY t.name",
                list(by_id),
            ):
                by_id[row["pair_id"]]["tags"].append(row["name"])
        unassigned = conn.execute(
            "SELECT COUNT(*) FROM generated_images g WHERE g.prompt_id=? "
            "AND NOT EXISTS(SELECT 1 FROM image_prompt_pairs ip "
            "WHERE ip.image_id=g.id)",
            (positive_id,),
        ).fetchone()[0]
        return {
            "positive": rows[0],
            "variants": variants,
            "unassigned_image_count": unassigned,
            "variant_total": variant_total,
            "variants_has_more": variants_has_more,
            "variants_next_key": variants[-1]["id"] if variants_has_more else None,
        }

    def get_library_options(self) -> dict:
        rows = (
            self.model.get_connection()
            .execute(
                "SELECT category FROM (SELECT p.category AS category "
                "FROM prompts p WHERE "
                + _ELIGIBLE
                + (" UNION SELECT category FROM prompt_pairs " "WHERE archived=0) ")
                + "WHERE category IS NOT NULL AND TRIM(category)!='' "
                "ORDER BY category COLLATE NOCASE,category"
            )
            .fetchall()
        )
        return {"categories": [row["category"] for row in rows]}

    def get_library_previews(self, positive_ids: list[int]) -> dict[int, list[dict]]:
        """Read at most two links per parent in one bounded page query."""
        if (
            not isinstance(positive_ids, list)
            or len(positive_ids) > 200
            or any(type(value) is not int or value <= 0 for value in positive_ids)
        ):
            raise ValueError("Invalid preview parent IDs")
        if not positive_ids:
            return {}
        result = {value: [] for value in positive_ids}
        marks = ",".join("?" for _ in positive_ids)
        rows = self.model.get_connection().execute(
            "WITH ranked AS (SELECT id,prompt_id,image_path,ROW_NUMBER() OVER "
            "(PARTITION BY prompt_id ORDER BY id DESC) AS position "
            f"FROM generated_images WHERE prompt_id IN ({marks})) "
            "SELECT id,prompt_id,image_path FROM ranked WHERE position<=2 "
            "ORDER BY prompt_id,id DESC",
            positive_ids,
        )
        for row in rows:
            result[row["prompt_id"]].append(dict(row))
        return result

    def list_library_images(
        self,
        positive_id: int,
        *,
        pair_id: int | None = None,
        limit: int = 40,
        after: int | None = None,
    ) -> dict:
        _limit(limit)
        conn = self.model.get_connection()
        if (
            pair_id is not None
            and not conn.execute(
                "SELECT 1 FROM prompt_pairs WHERE id=? "
                "AND positive_prompt_id=? AND archived=0",
                (pair_id, positive_id),
            ).fetchone()
        ):
            raise ValueError("Variant does not belong to this positive prompt")
        where, params = "g.prompt_id=?", [positive_id]
        if pair_id is not None:
            where += (
                " AND EXISTS(SELECT 1 FROM image_prompt_pairs ip "
                "WHERE ip.image_id=g.id AND ip.pair_id=?)"
            )
            params.append(pair_id)
        total = conn.execute(
            "SELECT COUNT(*) FROM generated_images g WHERE " + where, params
        ).fetchone()[0]
        if after is not None:
            if type(after) is not int or after <= 0:
                raise ValueError("Invalid image cursor")
            where += " AND g.id<?"
            params.append(after)
        rows = conn.execute(
            "SELECT g.id,g.image_path FROM generated_images g WHERE "
            + where
            + " ORDER BY g.id DESC LIMIT ?",
            [*params, limit + 1],
        ).fetchall()
        return {
            "images": [dict(row) for row in rows[:limit]],
            "total": total,
            "has_more": len(rows) > limit,
            "next_key": rows[limit - 1]["id"] if len(rows) > limit else None,
        }
