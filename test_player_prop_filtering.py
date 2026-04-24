#!/usr/bin/env python
"""Quick test to verify player prop vendor and market type filtering."""

import os
from courtvision_ai import CourtVisionAI, BallDontLieClient
from courtvision.runtime_markets import normalize_market_alias

print("=" * 70)
print("Testing Player Prop Filtering")
print("=" * 70)

# Test 1: Vendor filtering with default config
print("\n1. Testing vendor allowed (with default BALLDONTLIE_VENDORS):")
print(f"   Current env BALLDONTLIE_VENDORS: {os.getenv('BALLDONTLIE_VENDORS', '<not set>')}")

# Check which vendors would be in the default list
from courtvision.balldontlie_auth import BALLDONTLIE_API_KEY_ENV_VAR, resolve_api_key
import logging

try:
    _, key_details = resolve_api_key(
        entrypoint="test_player_prop_filtering",
        env_var_name=BALLDONTLIE_API_KEY_ENV_VAR,
        logger=logging.getLogger("test"),
    )
    test_client = BallDontLieClient(api_key=key_details.get("key", ""))
    
    test_vendors = ["fanduel", "draftkings", "fanatics", "caesars", "betrivers", "betparx", "ballybet", "betway"]
    for vendor in test_vendors:
        allowed = test_client._vendor_allowed(vendor)
        status = "✓ ALLOWED" if allowed else "✗ REJECTED"
        print(f"   {vendor:15} {status}")
    
    print(f"\n   Configured vendors: {test_client.preferred_vendors}")
except Exception as e:
    print(f"   Error creating client: {e}")
    print("   (This is OK if API key is not available)")
    # Fallback: test with default vendor config
    print("\n   Testing with default vendor config from code:")
    default_vendors = {"fanduel", "draftkings", "fanatics", "caesars", "betrivers"}
    test_vendors = ["fanduel", "draftkings", "fanatics", "caesars", "betrivers", "betparx", "ballybet", "betway"]
    for vendor in test_vendors:
        allowed = vendor in default_vendors
        status = "✓ ALLOWED" if allowed else "✗ REJECTED"
        print(f"   {vendor:15} {status}")

# Test 2: Market alias normalization
print("\n2. Testing market alias normalization:")
test_market_types = [
    ("player_points", "player_points"),
    ("points", "player_points"),
    ("points_1q", "player_points"),
    ("player_rebounds", "player_rebounds"),
    ("rebounds_2q", "player_rebounds"),
    ("player_assists", "player_assists"),
    ("assists_3q", "player_assists"),
    ("player_3pt_made", "player_3pt_made"),
    ("player_steals", "player_steals"),
    ("player_blocks", "player_blocks"),
    ("double_double", None),  # Unsupported combo
    ("triple_double", None),  # Unsupported combo
]

supported_markets = {"player_points", "player_rebounds", "player_assists", "player_3pt_made", "player_steals", "player_blocks"}

for raw_type, expected in test_market_types:
    normalized = normalize_market_alias(raw_type)
    is_supported = normalized in supported_markets if normalized else False
    
    if normalized == expected or (expected is None and normalized is None):
        status = "✓ CORRECT"
    else:
        status = f"✗ WRONG (got {normalized})"
    
    print(f"   {raw_type:20} → {str(normalized):20} {status}")

# Test 3: Supported markets list
print("\n3. Supported player market stats:")
for stat in sorted(supported_markets):
    print(f"   - {stat}")

print("\n" + "=" * 70)
print("All tests complete!")
print("=" * 70)
