#!/usr/bin/env python3
"""Analyze model routing cost effectiveness."""

import sqlite3
from datetime import datetime, timedelta
from collections import defaultdict
import json

DB_PATH = "data/lobs.db"

def analyze_model_routing():
    """Pull cost data and analyze routing decisions."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    
    # Get data from past 2 weeks
    two_weeks_ago = (datetime.now() - timedelta(days=14)).isoformat()
    
    print(f"Analyzing data since {two_weeks_ago}\n")
    
    # 1. Get worker runs with cost and model info
    cursor.execute("""
        SELECT 
            wr.id,
            wr.model,
            wr.total_cost_usd,
            wr.input_tokens,
            wr.output_tokens,
            wr.total_tokens,
            wr.succeeded,
            wr.task_id,
            wr.started_at,
            wr.summary,
            t.title as task_title,
            t.model_tier,
            t.escalation_tier,
            t.retry_count,
            t.status as task_status
        FROM worker_runs wr
        LEFT JOIN tasks t ON wr.task_id = t.id
        WHERE wr.started_at > ?
        ORDER BY wr.started_at DESC
    """, (two_weeks_ago,))
    
    runs = cursor.fetchall()
    
    # 2. Get task outcomes for more detailed success analysis
    cursor.execute("""
        SELECT 
            toutcome.*,
            t.model_tier,
            t.title as task_title
        FROM task_outcomes toutcome
        LEFT JOIN tasks t ON toutcome.task_id = t.id
        WHERE toutcome.created_at > ?
    """, (two_weeks_ago,))
    
    outcomes = cursor.fetchall()
    
    # 3. Aggregate statistics by model
    model_stats = defaultdict(lambda: {
        'runs': 0,
        'successes': 0,
        'failures': 0,
        'total_cost': 0.0,
        'total_tokens': 0,
        'tasks': []
    })
    
    for run in runs:
        model = run['model'] or 'unknown'
        stats = model_stats[model]
        stats['runs'] += 1
        stats['total_cost'] += run['total_cost_usd'] or 0.0
        stats['total_tokens'] += run['total_tokens'] or 0
        
        if run['succeeded'] is not None:
            if run['succeeded']:
                stats['successes'] += 1
            else:
                stats['failures'] += 1
        
        stats['tasks'].append({
            'id': run['task_id'],
            'title': run['task_title'],
            'cost': run['total_cost_usd'],
            'succeeded': run['succeeded'],
            'model_tier': run['model_tier'],
            'escalation': run['escalation_tier']
        })
    
    # 4. Aggregate by model tier (the routing decision)
    tier_stats = defaultdict(lambda: {
        'runs': 0,
        'successes': 0,
        'failures': 0,
        'total_cost': 0.0,
        'models_used': defaultdict(int)
    })
    
    for run in runs:
        tier = run['model_tier'] or 'auto'
        stats = tier_stats[tier]
        stats['runs'] += 1
        stats['total_cost'] += run['total_cost_usd'] or 0.0
        
        if run['succeeded'] is not None:
            if run['succeeded']:
                stats['successes'] += 1
            else:
                stats['failures'] += 1
        
        if run['model']:
            stats['models_used'][run['model']] += 1
    
    conn.close()
    
    return {
        'runs': [dict(r) for r in runs],
        'outcomes': [dict(o) for o in outcomes],
        'model_stats': dict(model_stats),
        'tier_stats': dict(tier_stats),
        'total_runs': len(runs),
        'date_range': {
            'start': two_weeks_ago,
            'end': datetime.now().isoformat()
        }
    }

def print_analysis(data):
    """Print formatted analysis."""
    print("=" * 80)
    print("MODEL ROUTING COST EFFECTIVENESS ANALYSIS")
    print("=" * 80)
    print(f"\nTotal runs analyzed: {data['total_runs']}")
    print(f"Date range: {data['date_range']['start'][:10]} to {data['date_range']['end'][:10]}")
    
    print("\n" + "=" * 80)
    print("ANALYSIS BY MODEL")
    print("=" * 80)
    
    # Sort by total runs
    sorted_models = sorted(
        data['model_stats'].items(),
        key=lambda x: x[1]['runs'],
        reverse=True
    )
    
    for model, stats in sorted_models:
        success_rate = (stats['successes'] / stats['runs'] * 100) if stats['runs'] > 0 else 0
        avg_cost = stats['total_cost'] / stats['runs'] if stats['runs'] > 0 else 0
        avg_tokens = stats['total_tokens'] / stats['runs'] if stats['runs'] > 0 else 0
        
        print(f"\n{model}")
        print(f"  Runs: {stats['runs']}")
        print(f"  Success rate: {success_rate:.1f}% ({stats['successes']} succeeded, {stats['failures']} failed)")
        print(f"  Total cost: ${stats['total_cost']:.4f}")
        print(f"  Avg cost per run: ${avg_cost:.4f}")
        print(f"  Avg tokens per run: {avg_tokens:,.0f}")
    
    print("\n" + "=" * 80)
    print("ANALYSIS BY MODEL TIER (routing decision)")
    print("=" * 80)
    
    sorted_tiers = sorted(
        data['tier_stats'].items(),
        key=lambda x: x[1]['runs'],
        reverse=True
    )
    
    for tier, stats in sorted_tiers:
        success_rate = (stats['successes'] / stats['runs'] * 100) if stats['runs'] > 0 else 0
        avg_cost = stats['total_cost'] / stats['runs'] if stats['runs'] > 0 else 0
        
        print(f"\n{tier.upper()}")
        print(f"  Runs: {stats['runs']}")
        print(f"  Success rate: {success_rate:.1f}% ({stats['successes']} succeeded, {stats['failures']} failed)")
        print(f"  Total cost: ${stats['total_cost']:.4f}")
        print(f"  Avg cost per run: ${avg_cost:.4f}")
        print(f"  Models used:")
        for model, count in sorted(stats['models_used'].items(), key=lambda x: x[1], reverse=True):
            print(f"    - {model}: {count} runs")

if __name__ == "__main__":
    data = analyze_model_routing()
    
    # Save raw data
    with open('model_routing_data.json', 'w') as f:
        json.dump(data, f, indent=2, default=str)
    print("Raw data saved to model_routing_data.json\n")
    
    # Print analysis
    print_analysis(data)
