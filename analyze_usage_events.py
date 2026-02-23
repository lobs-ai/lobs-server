#!/usr/bin/env python3
"""Analyze model usage events for cost tracking."""

import sqlite3
from datetime import datetime, timedelta
from collections import defaultdict
import json

DB_PATH = "data/lobs.db"

def analyze_usage_events():
    """Pull usage event data."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    
    # Get data from past 2 weeks
    two_weeks_ago = (datetime.now() - timedelta(days=14)).isoformat()
    
    print(f"Analyzing usage events since {two_weeks_ago}\n")
    
    # Get usage events
    cursor.execute("""
        SELECT 
            id,
            timestamp,
            source,
            provider,
            model,
            route_type,
            task_type,
            input_tokens,
            output_tokens,
            cached_tokens,
            requests,
            estimated_cost_usd,
            status
        FROM model_usage_events
        WHERE timestamp > ?
        ORDER BY timestamp DESC
    """, (two_weeks_ago,))
    
    events = cursor.fetchall()
    
    # Get pricing data
    cursor.execute("""
        SELECT 
            provider,
            model,
            route_type,
            input_per_1m_usd,
            output_per_1m_usd,
            cached_input_per_1m_usd,
            active,
            effective_date
        FROM model_pricing
        WHERE active = 1
        ORDER BY effective_date DESC
    """)
    
    pricing = cursor.fetchall()
    
    conn.close()
    
    # Aggregate by model
    model_stats = defaultdict(lambda: {
        'events': 0,
        'total_cost': 0.0,
        'input_tokens': 0,
        'output_tokens': 0,
        'cached_tokens': 0,
        'requests': 0,
        'successes': 0,
        'failures': 0
    })
    
    for event in events:
        model = f"{event['provider']}/{event['model']}"
        stats = model_stats[model]
        stats['events'] += 1
        stats['total_cost'] += event['estimated_cost_usd'] or 0.0
        stats['input_tokens'] += event['input_tokens'] or 0
        stats['output_tokens'] += event['output_tokens'] or 0
        stats['cached_tokens'] += event['cached_tokens'] or 0
        stats['requests'] += event['requests'] or 1
        
        if event['status'] == 'success':
            stats['successes'] += 1
        else:
            stats['failures'] += 1
    
    return {
        'events': [dict(e) for e in events],
        'pricing': [dict(p) for p in pricing],
        'model_stats': dict(model_stats),
        'total_events': len(events)
    }

def print_analysis(data):
    """Print formatted analysis."""
    print("=" * 80)
    print("MODEL USAGE EVENTS ANALYSIS")
    print("=" * 80)
    print(f"\nTotal events: {data['total_events']}")
    
    if data['total_events'] == 0:
        print("\n⚠️  No usage events found in the database.")
        print("This suggests that model usage tracking may not be implemented yet.")
        return
    
    print("\n" + "=" * 80)
    print("PRICING DATA")
    print("=" * 80)
    
    if not data['pricing']:
        print("\n⚠️  No pricing data found in model_pricing table.")
    else:
        for p in data['pricing']:
            print(f"\n{p['provider']}/{p['model']} ({p['route_type']})")
            print(f"  Input: ${p['input_per_1m_usd']:.2f}/1M tokens")
            print(f"  Output: ${p['output_per_1m_usd']:.2f}/1M tokens")
            if p['cached_input_per_1m_usd']:
                print(f"  Cached: ${p['cached_input_per_1m_usd']:.2f}/1M tokens")
    
    print("\n" + "=" * 80)
    print("ANALYSIS BY MODEL")
    print("=" * 80)
    
    sorted_models = sorted(
        data['model_stats'].items(),
        key=lambda x: x[1]['total_cost'],
        reverse=True
    )
    
    total_cost = sum(s['total_cost'] for s in data['model_stats'].values())
    print(f"\nTotal cost across all models: ${total_cost:.4f}")
    
    for model, stats in sorted_models:
        success_rate = (stats['successes'] / stats['events'] * 100) if stats['events'] > 0 else 0
        cost_per_request = stats['total_cost'] / stats['requests'] if stats['requests'] > 0 else 0
        
        print(f"\n{model}")
        print(f"  Events: {stats['events']}")
        print(f"  Requests: {stats['requests']}")
        print(f"  Success rate: {success_rate:.1f}% ({stats['successes']} succeeded, {stats['failures']} failed)")
        print(f"  Total cost: ${stats['total_cost']:.4f}")
        print(f"  Cost per request: ${cost_per_request:.6f}")
        print(f"  Input tokens: {stats['input_tokens']:,}")
        print(f"  Output tokens: {stats['output_tokens']:,}")
        if stats['cached_tokens'] > 0:
            print(f"  Cached tokens: {stats['cached_tokens']:,}")

if __name__ == "__main__":
    data = analyze_usage_events()
    
    # Save raw data
    with open('usage_events_data.json', 'w') as f:
        json.dump(data, f, indent=2, default=str)
    print("Raw data saved to usage_events_data.json\n")
    
    # Print analysis
    print_analysis(data)
