#!/usr/bin/env python3
"""
Memory Search Quality Benchmark

Runs test queries against the memory search API and compares results
against expected outcomes. Use this to measure improvement after
implementing vector search.

Usage:
    python benchmark.py                    # Run all tests
    python benchmark.py --semantic         # Test semantic search (after implementation)
    python benchmark.py --query "SwiftUI"  # Test single query
"""

import json
import sys
import asyncio
from pathlib import Path
from typing import Optional

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from app.database import get_db
from app.routers.memories import search_memories
from sqlalchemy import select
from app.models import Memory


async def load_test_queries():
    """Load test queries from JSON file."""
    test_file = Path(__file__).parent / "test-queries.json"
    with open(test_file) as f:
        return json.load(f)


async def run_search(query: str, semantic: bool = False):
    """Run a search query against the memory system."""
    async for db in get_db():
        results = await search_memories(
            q=query,
            semantic=semantic,
            limit=10,
            db=db
        )
        return results


def calculate_precision_at_k(results, expected, k=3):
    """
    Calculate precision@k: proportion of top-k results that are relevant.
    
    Args:
        results: List of search results
        expected: List of expected result dicts with 'path' and 'agent'
        k: Number of top results to consider
    
    Returns:
        float: Precision score (0.0 to 1.0)
    """
    if not expected:
        # No expected results — check if we correctly returned nothing
        return 1.0 if not results else 0.0
    
    if not results:
        return 0.0
    
    top_k = results[:k]
    relevant_count = 0
    
    for result in top_k:
        for exp in expected:
            if (result.path == exp["path"] and 
                result.agent == exp["agent"]):
                relevant_count += 1
                break
    
    return relevant_count / min(k, len(expected))


def calculate_recall(results, expected):
    """
    Calculate recall: proportion of expected results that were found.
    
    Returns:
        float: Recall score (0.0 to 1.0)
    """
    if not expected:
        return 1.0
    
    if not results:
        return 0.0
    
    found_count = 0
    for exp in expected:
        for result in results:
            if (result.path == exp["path"] and 
                result.agent == exp["agent"]):
                found_count += 1
                break
    
    return found_count / len(expected)


async def benchmark_single_query(query_data: dict, semantic: bool = False):
    """Run benchmark for a single query."""
    query = query_data["query"]
    expected = query_data["expected_results"]
    
    print(f"\n{'='*60}")
    print(f"Query: {query}")
    print(f"Expected: {len(expected)} results")
    
    results = await run_search(query, semantic=semantic)
    
    print(f"Found: {len(results)} results")
    
    if results:
        print("\nTop 3 results:")
        for i, r in enumerate(results[:3], 1):
            print(f"  {i}. {r.title} ({r.agent})")
            print(f"     Score: {r.score:.3f}")
            print(f"     Path: {r.path}")
    
    precision = calculate_precision_at_k(results, expected, k=3)
    recall = calculate_recall(results, expected)
    
    print(f"\nMetrics:")
    print(f"  Precision@3: {precision:.2f}")
    print(f"  Recall: {recall:.2f}")
    print(f"  Current baseline: {query_data['current_score']:.2f}")
    print(f"  Target: {query_data['target_score']:.2f}")
    
    # Check if we met target
    if precision >= query_data['target_score']:
        print(f"  ✓ PASSED (met target)")
        return True
    else:
        gap = query_data['target_score'] - precision
        print(f"  ✗ FAILED (gap: {gap:.2f})")
        return False


async def benchmark_all(semantic: bool = False):
    """Run benchmark on all test queries."""
    queries = await load_test_queries()
    
    print("="*60)
    print("MEMORY SEARCH QUALITY BENCHMARK")
    print("="*60)
    print(f"Mode: {'SEMANTIC' if semantic else 'TEXT'} search")
    print(f"Test queries: {len(queries)}")
    print()
    
    passed = 0
    failed = 0
    total_precision = 0
    total_recall = 0
    
    for query_data in queries:
        success = await benchmark_single_query(query_data, semantic=semantic)
        if success:
            passed += 1
        else:
            failed += 1
    
    # Summary
    print("\n" + "="*60)
    print("SUMMARY")
    print("="*60)
    print(f"Passed: {passed}/{len(queries)} ({passed/len(queries)*100:.1f}%)")
    print(f"Failed: {failed}/{len(queries)} ({failed/len(queries)*100:.1f}%)")
    
    if semantic:
        print("\n✓ Semantic search benchmark complete")
    else:
        print("\n⚠ Text search baseline established")
        print("Run with --semantic after implementing vector search to measure improvement")


async def main():
    """Main entry point."""
    import argparse
    
    parser = argparse.ArgumentParser(description="Memory search quality benchmark")
    parser.add_argument("--semantic", action="store_true", help="Use semantic search")
    parser.add_argument("--query", type=str, help="Run single query")
    args = parser.parse_args()
    
    if args.query:
        # Single query test
        queries = await load_test_queries()
        query_data = next((q for q in queries if q["query"] == args.query), None)
        if query_data:
            await benchmark_single_query(query_data, semantic=args.semantic)
        else:
            print(f"Query '{args.query}' not found in test set")
            print("\nAvailable queries:")
            for q in queries:
                print(f"  - {q['query']}")
    else:
        # Full benchmark
        await benchmark_all(semantic=args.semantic)


if __name__ == "__main__":
    asyncio.run(main())
