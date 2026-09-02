"""Seed initial JSON rulesets (from ``rulesets/*.json``) into the merged RuleStore —
both the authoritative ruleset repository and the vector index, in one call per file.
"""
import json
from pathlib import Path
import logging

logger = logging.getLogger(__name__)


def seed_from_directory(store, directory: Path) -> dict:
    """
    Load each JSON file, skip if ruleset_id already exists.
    Returns {"seeded": [...], "skipped": [...]}.
    """
    results = {"seeded": [], "skipped": []}

    if not directory.exists():
        logger.warning(f"Seed directory {directory} does not exist.")
        return results

    existing_ids = set(store.ruleset_ids())

    for file_path in sorted(directory.glob("*.json")):
        with open(file_path, "r", encoding="utf-8") as f:
            content = json.load(f)

        ruleset_id = content.get("ruleset_id")
        if not ruleset_id:
            logger.warning(f"File {file_path} missing ruleset_id, skipping.")
            continue

        if ruleset_id in existing_ids:
            results["skipped"].append(ruleset_id)
            continue

        try:
            # Publishes version 1 in the authoritative store AND indexes every rule
            # into the vector store — one call, both stores updated.
            store.create_ruleset(ruleset_id, content, published_by="system_seed")
            results["seeded"].append(ruleset_id)
        except Exception as e:
            logger.error(f"Failed to seed {ruleset_id}: {e}")

    return results
