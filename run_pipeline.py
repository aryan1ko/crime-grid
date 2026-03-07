"""
run_pipeline.py

Single entry point to run the full Austin Crime Ontology pipeline.

Usage:
    python run_pipeline.py                  # Run all stages
    python run_pipeline.py --stage ingest   # Run only ingestion
    python run_pipeline.py --stage build    # Run only object build
    python run_pipeline.py --stage analyze  # Run only analysis + viz
    python run_pipeline.py --skip-census    # Skip Census fetch (no API key)
"""

import argparse
import sys
import time
from loguru import logger

import config


def run_ingestion(skip_census: bool = False):
    logger.info("=" * 50)
    logger.info("STAGE 1: INGESTION")
    logger.info("=" * 50)

    from ingestion.fetch_crime import main as fetch_crime
    fetch_crime()

    from ingestion.fetch_boundaries import main as fetch_boundaries
    try:
        fetch_boundaries()
    except Exception as exc:
        logger.warning(f"Boundary fetch failed; continuing without district geometry: {exc}")

    if not skip_census:
        if not config.CENSUS_API_KEY:
            logger.warning("No CENSUS_API_KEY set — skipping Census fetch.")
            logger.warning("Get a free key at: https://api.census.gov/data/key_signup.html")
        else:
            from ingestion.fetch_census import main as fetch_census
            try:
                fetch_census()
            except Exception as exc:
                logger.warning(f"Census fetch failed; continuing without demographics: {exc}")
    else:
        logger.info("Skipping Census fetch (--skip-census flag set)")


def run_ontology_build():
    logger.info("=" * 50)
    logger.info("STAGE 2: ONTOLOGY BUILD")
    logger.info("=" * 50)

    from ontology.build_objects import main as build_objects
    build_objects()

    from ontology.entity_resolution import main as entity_resolution
    entity_resolution()

    from ontology.link_builder import main as link_builder
    link_builder()


def run_analysis():
    logger.info("=" * 50)
    logger.info("STAGE 3: ANALYSIS + VISUALIZATION")
    logger.info("=" * 50)

    from analysis.queries import print_all
    print_all()

    from viz.map_builder import main as map_builder
    map_builder()


def main():
    parser = argparse.ArgumentParser(description="Austin Crime Ontology Pipeline")
    parser.add_argument(
        "--stage",
        choices=["ingest", "build", "analyze", "all"],
        default="all",
        help="Which pipeline stage to run",
    )
    parser.add_argument(
        "--skip-census",
        action="store_true",
        help="Skip Census API fetch (useful if no API key)",
    )
    args = parser.parse_args()

    start = time.time()
    logger.info("Austin Crime Ontology — Pipeline Start")
    logger.info(f"Config: years {config.MIN_YEAR}–{config.MAX_YEAR}, DB: {config.DB_PATH}")

    try:
        if args.stage in ("ingest", "all"):
            run_ingestion(skip_census=args.skip_census)

        if args.stage in ("build", "all"):
            run_ontology_build()

        if args.stage in ("analyze", "all"):
            run_analysis()

    except FileNotFoundError as e:
        logger.error(f"Missing input file: {e}")
        logger.error("Make sure to run ingestion before build, and build before analyze.")
        sys.exit(1)
    except Exception as e:
        logger.exception(f"Pipeline failed: {e}")
        sys.exit(1)

    elapsed = time.time() - start
    logger.success(f"Pipeline complete in {elapsed:.1f}s")
    logger.info(f"Outputs saved to: {config.OUTPUT_DIR}")
    logger.info("Open data/outputs/*.html in your browser to view maps.")


if __name__ == "__main__":
    main()
