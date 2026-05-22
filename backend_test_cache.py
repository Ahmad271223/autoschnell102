#!/usr/bin/env python3
"""
Backend API Testing for Unified Listing Cache Feature
Tests the cache logic with source + item_id as key for mobile.de and kleinanzeigen.de
"""

import requests
import json
import sys
import time
from typing import Dict, Any, Optional, Tuple

# Backend URL from frontend/.env
BACKEND_URL = "https://vehicle-holder-auto.preview.emergentagent.com/api"

# Test credentials from /app/memory/test_credentials.md
ADMIN_EMAIL = "admin@autohandel.app"
ADMIN_PASSWORD = "Admin123!"

class CacheTester:
    def __init__(self):
        self.session = requests.Session()
        self.token = None
        self.headers = {}
        self.test_results = []
        
    def log_result(self, test_name: str, passed: bool, details: str = ""):
        """Log test result"""
        status = "✅ PASS" if passed else "❌ FAIL"
        print(f"{status} - {test_name}")
        if details:
            print(f"    {details}")
        self.test_results.append({
            "test": test_name,
            "passed": passed,
            "details": details
        })
        
    def login(self) -> bool:
        """Login and get authentication token"""
        print("\n🔐 Testing login...")
        
        login_data = {
            "email": ADMIN_EMAIL,
            "password": ADMIN_PASSWORD
        }
        
        try:
            response = self.session.post(
                f"{BACKEND_URL}/auth/login",
                json=login_data,
                headers={"Content-Type": "application/json"},
                timeout=10
            )
            
            if response.status_code == 200:
                data = response.json()
                self.token = data.get("token")
                if self.token:
                    self.headers = {"Authorization": f"Bearer {self.token}"}
                    self.log_result("Login", True, f"Token received")
                    return True
                else:
                    self.log_result("Login", False, "No token in response")
                    return False
            else:
                self.log_result("Login", False, f"Status {response.status_code}: {response.text[:200]}")
                return False
                
        except Exception as e:
            self.log_result("Login", False, f"Exception: {e}")
            return False
    
    def test_extract_kleinanzeigen(self) -> bool:
        """Test A1: Extract identity from kleinanzeigen.de URL"""
        print("\n📋 A1: Testing kleinanzeigen.de identity extraction...")
        
        test_url = "https://www.kleinanzeigen.de/s-anzeige/mazda-cx-5/3395964748-216-7219"
        expected = {
            "source": "kleinanzeigen",
            "item_id": "3395964748",
            "cache_key": "kleinanzeigen:3395964748"
        }
        
        try:
            response = self.session.post(
                f"{BACKEND_URL}/listings/extract",
                json={"url": test_url},
                headers={"Content-Type": "application/json"},
                timeout=10
            )
            
            if response.status_code == 200:
                data = response.json()
                if (data.get("source") == expected["source"] and 
                    data.get("item_id") == expected["item_id"] and 
                    data.get("cache_key") == expected["cache_key"]):
                    self.log_result("A1: Kleinanzeigen Extract", True, 
                                  f"Correct: {data}")
                    return True
                else:
                    self.log_result("A1: Kleinanzeigen Extract", False, 
                                  f"Expected {expected}, got {data}")
                    return False
            else:
                self.log_result("A1: Kleinanzeigen Extract", False, 
                              f"Status {response.status_code}: {response.text[:200]}")
                return False
                
        except Exception as e:
            self.log_result("A1: Kleinanzeigen Extract", False, f"Exception: {e}")
            return False
    
    def test_extract_mobile_query(self) -> bool:
        """Test A2: Extract identity from mobile.de query URL"""
        print("\n📋 A2: Testing mobile.de query URL identity extraction...")
        
        test_url = "https://suchen.mobile.de/fahrzeuge/details.html?id=454337945&action=eyeCatcher"
        expected = {
            "source": "mobile",
            "item_id": "454337945",
            "cache_key": "mobile:454337945"
        }
        
        try:
            response = self.session.post(
                f"{BACKEND_URL}/listings/extract",
                json={"url": test_url},
                headers={"Content-Type": "application/json"},
                timeout=10
            )
            
            if response.status_code == 200:
                data = response.json()
                if (data.get("source") == expected["source"] and 
                    data.get("item_id") == expected["item_id"] and 
                    data.get("cache_key") == expected["cache_key"]):
                    self.log_result("A2: Mobile Query Extract", True, 
                                  f"Correct: {data}")
                    return True
                else:
                    self.log_result("A2: Mobile Query Extract", False, 
                                  f"Expected {expected}, got {data}")
                    return False
            else:
                self.log_result("A2: Mobile Query Extract", False, 
                              f"Status {response.status_code}: {response.text[:200]}")
                return False
                
        except Exception as e:
            self.log_result("A2: Mobile Query Extract", False, f"Exception: {e}")
            return False
    
    def test_extract_mobile_pretty(self) -> bool:
        """Test A3: Extract identity from mobile.de pretty URL"""
        print("\n📋 A3: Testing mobile.de pretty URL identity extraction...")
        
        test_url = "https://suchen.mobile.de/auto-inserat/ford-focus/448651862.html"
        expected = {
            "source": "mobile",
            "item_id": "448651862",
            "cache_key": "mobile:448651862"
        }
        
        try:
            response = self.session.post(
                f"{BACKEND_URL}/listings/extract",
                json={"url": test_url},
                headers={"Content-Type": "application/json"},
                timeout=10
            )
            
            if response.status_code == 200:
                data = response.json()
                if (data.get("source") == expected["source"] and 
                    data.get("item_id") == expected["item_id"] and 
                    data.get("cache_key") == expected["cache_key"]):
                    self.log_result("A3: Mobile Pretty Extract", True, 
                                  f"Correct: {data}")
                    return True
                else:
                    self.log_result("A3: Mobile Pretty Extract", False, 
                                  f"Expected {expected}, got {data}")
                    return False
            else:
                self.log_result("A3: Mobile Pretty Extract", False, 
                              f"Status {response.status_code}: {response.text[:200]}")
                return False
                
        except Exception as e:
            self.log_result("A3: Mobile Pretty Extract", False, f"Exception: {e}")
            return False
    
    def test_extract_autoscout(self) -> bool:
        """Test A4: Extract identity from AutoScout24 URL"""
        print("\n📋 A4: Testing AutoScout24 URL identity extraction...")
        
        test_url = "https://www.autoscout24.de/angebote/mercedes-benz-c-180-blubla-d4dd34a4-1795-4bd8-a7d8-064f3b73d8f5?abc=1"
        expected = {
            "source": "autoscout24",
            "item_id": "d4dd34a4-1795-4bd8-a7d8-064f3b73d8f5",
            "cache_key": "autoscout24:d4dd34a4-1795-4bd8-a7d8-064f3b73d8f5"
        }
        
        try:
            response = self.session.post(
                f"{BACKEND_URL}/listings/extract",
                json={"url": test_url},
                headers={"Content-Type": "application/json"},
                timeout=10
            )
            
            if response.status_code == 200:
                data = response.json()
                if (data.get("source") == expected["source"] and 
                    data.get("item_id") == expected["item_id"] and 
                    data.get("cache_key") == expected["cache_key"]):
                    self.log_result("A4: AutoScout Extract", True, 
                                  f"Correct: {data}")
                    return True
                else:
                    self.log_result("A4: AutoScout Extract", False, 
                                  f"Expected {expected}, got {data}")
                    return False
            else:
                self.log_result("A4: AutoScout Extract", False, 
                              f"Status {response.status_code}: {response.text[:200]}")
                return False
                
        except Exception as e:
            self.log_result("A4: AutoScout Extract", False, f"Exception: {e}")
            return False
    
    def test_extract_invalid_url(self) -> bool:
        """Test A5: Extract identity from invalid URL (should return 400)"""
        print("\n📋 A5: Testing invalid URL rejection...")
        
        test_url = "https://example.com/foo"
        
        try:
            response = self.session.post(
                f"{BACKEND_URL}/listings/extract",
                json={"url": test_url},
                headers={"Content-Type": "application/json"},
                timeout=10
            )
            
            if response.status_code == 400:
                self.log_result("A5: Invalid URL Rejection", True, 
                              f"Correctly rejected with 400: {response.text[:100]}")
                return True
            else:
                self.log_result("A5: Invalid URL Rejection", False, 
                              f"Expected 400, got {response.status_code}")
                return False
                
        except Exception as e:
            self.log_result("A5: Invalid URL Rejection", False, f"Exception: {e}")
            return False
    
    def test_compare_cache_logic(self) -> bool:
        """Test B: /api/mobile/compare cache logic - main focus"""
        print("\n🚗 B: Testing /api/mobile/compare cache logic...")
        
        # Use a sandbox mobile.de URL - ID 1000006 is in sandbox_data.xml
        test_url = "https://suchen.mobile.de/fahrzeuge/details.html?id=1000006"
        
        try:
            # Call 1: First call should be cache MISS (cached=false)
            print("  Call 1: First call (expecting cached=false)...")
            response1 = self.session.post(
                f"{BACKEND_URL}/mobile/compare",
                json={"url": test_url},
                headers=self.headers,
                timeout=15
            )
            
            if response1.status_code != 200:
                self.log_result("B1: Compare First Call", False, 
                              f"Status {response1.status_code}: {response1.text[:200]}")
                return False
            
            data1 = response1.json()
            
            # Verify response structure
            if not all(k in data1 for k in ["cached", "cache_key", "source", "vehicle", "ad_id"]):
                self.log_result("B1: Compare First Call", False, 
                              f"Missing required fields in response: {list(data1.keys())}")
                return False
            
            if data1.get("cached") != False:
                self.log_result("B1: Compare First Call", False, 
                              f"Expected cached=false, got cached={data1.get('cached')}")
                return False
            
            if data1.get("source") != "mobile":
                self.log_result("B1: Compare First Call", False, 
                              f"Expected source=mobile, got source={data1.get('source')}")
                return False
            
            cache_key1 = data1.get("cache_key")
            vehicle1 = data1.get("vehicle")
            ad_id1 = data1.get("ad_id")
            
            self.log_result("B1: Compare First Call", True, 
                          f"cached=false, cache_key={cache_key1}, ad_id={ad_id1}")
            
            # Small delay to ensure DB write completes
            time.sleep(0.5)
            
            # Call 2: Same URL should be cache HIT (cached=true)
            print("  Call 2: Same URL (expecting cached=true)...")
            response2 = self.session.post(
                f"{BACKEND_URL}/mobile/compare",
                json={"url": test_url},
                headers=self.headers,
                timeout=15
            )
            
            if response2.status_code != 200:
                self.log_result("B2: Compare Second Call", False, 
                              f"Status {response2.status_code}: {response2.text[:200]}")
                return False
            
            data2 = response2.json()
            
            if data2.get("cached") != True:
                self.log_result("B2: Compare Second Call", False, 
                              f"Expected cached=true, got cached={data2.get('cached')}")
                return False
            
            if data2.get("cache_key") != cache_key1:
                self.log_result("B2: Compare Second Call", False, 
                              f"Cache key mismatch: {cache_key1} vs {data2.get('cache_key')}")
                return False
            
            self.log_result("B2: Compare Second Call", True, 
                          f"cached=true, same cache_key={cache_key1}")
            
            # Call 3: Same URL with tracking params should still be cache HIT
            print("  Call 3: Same URL with tracking params (expecting cached=true)...")
            test_url_with_params = f"{test_url}&utm_source=test&ref=foo"
            response3 = self.session.post(
                f"{BACKEND_URL}/mobile/compare",
                json={"url": test_url_with_params},
                headers=self.headers,
                timeout=15
            )
            
            if response3.status_code != 200:
                self.log_result("B3: Compare With Tracking Params", False, 
                              f"Status {response3.status_code}: {response3.text[:200]}")
                return False
            
            data3 = response3.json()
            
            if data3.get("cached") != True:
                self.log_result("B3: Compare With Tracking Params", False, 
                              f"Expected cached=true (same item_id), got cached={data3.get('cached')}")
                return False
            
            if data3.get("cache_key") != cache_key1:
                self.log_result("B3: Compare With Tracking Params", False, 
                              f"Cache key mismatch: {cache_key1} vs {data3.get('cache_key')}")
                return False
            
            self.log_result("B3: Compare With Tracking Params", True, 
                          f"cached=true despite different URL params - CORE FEATURE WORKING!")
            
            # Call 4: Pretty URL variant with same ID should also be cache HIT
            print("  Call 4: Pretty URL variant (expecting cached=true)...")
            test_url_pretty = "https://suchen.mobile.de/auto-inserat/test-vehicle/1000006.html"
            response4 = self.session.post(
                f"{BACKEND_URL}/mobile/compare",
                json={"url": test_url_pretty},
                headers=self.headers,
                timeout=15
            )
            
            if response4.status_code != 200:
                self.log_result("B4: Compare Pretty URL Variant", False, 
                              f"Status {response4.status_code}: {response4.text[:200]}")
                return False
            
            data4 = response4.json()
            
            if data4.get("cached") != True:
                self.log_result("B4: Compare Pretty URL Variant", False, 
                              f"Expected cached=true (same item_id), got cached={data4.get('cached')}")
                return False
            
            if data4.get("cache_key") != cache_key1:
                self.log_result("B4: Compare Pretty URL Variant", False, 
                              f"Cache key mismatch: {cache_key1} vs {data4.get('cache_key')}")
                return False
            
            self.log_result("B4: Compare Pretty URL Variant", True, 
                          f"cached=true for pretty URL variant - URL normalization working!")
            
            return True
                
        except Exception as e:
            self.log_result("B: Compare Cache Logic", False, f"Exception: {e}")
            return False
    
    def test_compare_autoscout_rejection(self) -> bool:
        """Test C: AutoScout24 URL in compare should return 400"""
        print("\n🚫 C: Testing AutoScout24 rejection in compare...")
        
        test_url = "https://www.autoscout24.de/angebote/mercedes-benz-c-180-test-d4dd34a4-1795-4bd8-a7d8-064f3b73d8f5"
        
        try:
            response = self.session.post(
                f"{BACKEND_URL}/mobile/compare",
                json={"url": test_url},
                headers=self.headers,
                timeout=10
            )
            
            if response.status_code == 400:
                error_text = response.text
                if "AutoScout24" in error_text and "nicht angebunden" in error_text:
                    self.log_result("C: AutoScout24 Rejection", True, 
                                  f"Correctly rejected: {error_text[:150]}")
                    return True
                else:
                    self.log_result("C: AutoScout24 Rejection", False, 
                                  f"400 but wrong message: {error_text[:150]}")
                    return False
            else:
                self.log_result("C: AutoScout24 Rejection", False, 
                              f"Expected 400, got {response.status_code}")
                return False
                
        except Exception as e:
            self.log_result("C: AutoScout24 Rejection", False, f"Exception: {e}")
            return False
    
    def test_resolve_cache_logic(self) -> bool:
        """Test D: /api/listings/resolve cache logic"""
        print("\n🔄 D: Testing /api/listings/resolve cache logic...")
        
        # Use a different sandbox ID to avoid interference with compare tests
        test_url = "https://suchen.mobile.de/fahrzeuge/details.html?id=1000007"
        
        try:
            # Call 1: First call should be cache MISS
            print("  Call 1: First resolve (expecting cached=false)...")
            response1 = self.session.post(
                f"{BACKEND_URL}/listings/resolve",
                json={"url": test_url},
                headers=self.headers,
                timeout=15
            )
            
            if response1.status_code != 200:
                self.log_result("D1: Resolve First Call", False, 
                              f"Status {response1.status_code}: {response1.text[:200]}")
                return False
            
            data1 = response1.json()
            
            if not all(k in data1 for k in ["cached", "cache_key", "source", "item_id", "vehicle"]):
                self.log_result("D1: Resolve First Call", False, 
                              f"Missing required fields: {list(data1.keys())}")
                return False
            
            if data1.get("cached") != False:
                self.log_result("D1: Resolve First Call", False, 
                              f"Expected cached=false, got cached={data1.get('cached')}")
                return False
            
            cache_key1 = data1.get("cache_key")
            
            self.log_result("D1: Resolve First Call", True, 
                          f"cached=false, cache_key={cache_key1}")
            
            # Small delay
            time.sleep(0.5)
            
            # Call 2: Same URL should be cache HIT
            print("  Call 2: Second resolve (expecting cached=true)...")
            response2 = self.session.post(
                f"{BACKEND_URL}/listings/resolve",
                json={"url": test_url},
                headers=self.headers,
                timeout=15
            )
            
            if response2.status_code != 200:
                self.log_result("D2: Resolve Second Call", False, 
                              f"Status {response2.status_code}: {response2.text[:200]}")
                return False
            
            data2 = response2.json()
            
            if data2.get("cached") != True:
                self.log_result("D2: Resolve Second Call", False, 
                              f"Expected cached=true, got cached={data2.get('cached')}")
                return False
            
            if data2.get("cache_key") != cache_key1:
                self.log_result("D2: Resolve Second Call", False, 
                              f"Cache key mismatch: {cache_key1} vs {data2.get('cache_key')}")
                return False
            
            self.log_result("D2: Resolve Second Call", True, 
                          f"cached=true, vehicle from cache")
            
            return True
                
        except Exception as e:
            self.log_result("D: Resolve Cache Logic", False, f"Exception: {e}")
            return False
    
    def test_compare_no_regression(self) -> bool:
        """Test F: Verify /api/mobile/compare still returns all expected fields"""
        print("\n✅ F: Testing no regression in compare response...")
        
        test_url = "https://suchen.mobile.de/fahrzeuge/details.html?id=1000008"
        
        try:
            response = self.session.post(
                f"{BACKEND_URL}/mobile/compare",
                json={"url": test_url},
                headers=self.headers,
                timeout=15
            )
            
            if response.status_code != 200:
                self.log_result("F: No Regression", False, 
                              f"Status {response.status_code}: {response.text[:200]}")
                return False
            
            data = response.json()
            
            # Check all expected fields are present
            required_fields = ["vehicle_id", "ad_id", "vehicle", "search_url", 
                             "rules_applied", "source", "cached", "cache_key"]
            
            missing_fields = [f for f in required_fields if f not in data]
            
            if missing_fields:
                self.log_result("F: No Regression", False, 
                              f"Missing fields: {missing_fields}")
                return False
            
            # Verify snapshot_id is present (can be None)
            if "snapshot_id" not in data:
                self.log_result("F: No Regression", False, 
                              "Missing snapshot_id field")
                return False
            
            self.log_result("F: No Regression", True, 
                          f"All expected fields present: {list(data.keys())}")
            return True
                
        except Exception as e:
            self.log_result("F: No Regression", False, f"Exception: {e}")
            return False
    
    def test_extract_no_auth_required(self) -> bool:
        """Test that /api/listings/extract does NOT require auth"""
        print("\n🔓 Testing /api/listings/extract without auth...")
        
        test_url = "https://www.kleinanzeigen.de/s-anzeige/test/1234567890-216-7219"
        
        try:
            # Make request WITHOUT auth headers
            response = self.session.post(
                f"{BACKEND_URL}/listings/extract",
                json={"url": test_url},
                headers={"Content-Type": "application/json"},  # No Authorization header
                timeout=10
            )
            
            # Should work without auth (200 or 400 for invalid URL, but NOT 401)
            if response.status_code in [200, 400]:
                self.log_result("Extract No Auth Required", True, 
                              f"Works without auth (status {response.status_code})")
                return True
            elif response.status_code == 401:
                self.log_result("Extract No Auth Required", False, 
                              "Incorrectly requires auth (401)")
                return False
            else:
                self.log_result("Extract No Auth Required", False, 
                              f"Unexpected status {response.status_code}")
                return False
                
        except Exception as e:
            self.log_result("Extract No Auth Required", False, f"Exception: {e}")
            return False
    
    def run_all_tests(self):
        """Run all tests in sequence"""
        print("🚀 Starting Unified Listing Cache Backend Tests")
        print("=" * 70)
        
        # Test 0: Login
        if not self.login():
            print("\n❌ Login failed - cannot continue with auth-required tests")
            print("Will still run non-auth tests...")
        
        # Test that extract doesn't require auth
        self.test_extract_no_auth_required()
        
        # Test A: Identity extraction (no auth required)
        print("\n" + "=" * 70)
        print("SCENARIO A: Identity Extraction (/api/listings/extract)")
        print("=" * 70)
        self.test_extract_kleinanzeigen()
        self.test_extract_mobile_query()
        self.test_extract_mobile_pretty()
        self.test_extract_autoscout()
        self.test_extract_invalid_url()
        
        if not self.token:
            print("\n⚠️  Skipping auth-required tests (no token)")
            self.print_summary()
            return
        
        # Test B: Compare cache logic (main focus)
        print("\n" + "=" * 70)
        print("SCENARIO B: /api/mobile/compare Cache Logic (MAIN FOCUS)")
        print("=" * 70)
        self.test_compare_cache_logic()
        
        # Test C: AutoScout24 rejection
        print("\n" + "=" * 70)
        print("SCENARIO C: AutoScout24 Rejection")
        print("=" * 70)
        self.test_compare_autoscout_rejection()
        
        # Test D: Resolve cache logic
        print("\n" + "=" * 70)
        print("SCENARIO D: /api/listings/resolve Cache Logic")
        print("=" * 70)
        self.test_resolve_cache_logic()
        
        # Test F: No regression
        print("\n" + "=" * 70)
        print("SCENARIO F: No Regression Check")
        print("=" * 70)
        self.test_compare_no_regression()
        
        # Print summary
        self.print_summary()
    
    def print_summary(self):
        """Print test results summary"""
        print("\n" + "=" * 70)
        print("📊 TEST RESULTS SUMMARY")
        print("=" * 70)
        
        passed = sum(1 for r in self.test_results if r["passed"])
        total = len(self.test_results)
        
        for result in self.test_results:
            status = "✅ PASS" if result["passed"] else "❌ FAIL"
            print(f"{status} - {result['test']}")
        
        print(f"\nOverall: {passed}/{total} tests passed")
        
        if passed == total:
            print("🎉 All tests passed!")
        else:
            print("⚠️  Some tests failed - check details above")

def main():
    """Main test runner"""
    tester = CacheTester()
    tester.run_all_tests()
    
    # Exit with appropriate code
    passed = sum(1 for r in tester.test_results if r["passed"])
    total = len(tester.test_results)
    sys.exit(0 if passed == total else 1)

if __name__ == "__main__":
    main()
