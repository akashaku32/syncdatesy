"""
Dynamic Salesforce Object Queryability Checker

This module uses Salesforce APIs to dynamically determine if objects are queryable,
eliminating the need for hardcoded lists or configuration files.
"""

import logging
import time
from functools import lru_cache, wraps
from typing import Dict, Set, Optional, Tuple, Any
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)

class SalesforceObjectChecker:
    """Dynamic checker for Salesforce object queryability using API calls."""
    
    def __init__(self, sf_client, cache_ttl_minutes: int = 60):
        """
        Initialize the dynamic checker.
        
        Args:
            sf_client: Authenticated Salesforce client instance
            cache_ttl_minutes: How long to cache results (default 60 minutes)
        """
        self.sf_client = sf_client
        self.cache_ttl = timedelta(minutes=cache_ttl_minutes)
        self._object_cache = {}
        self._cache_timestamps = {}
        self._global_describe_cache = None
        self._global_describe_timestamp = None
        
        # Minimal blacklist for objects that cause API issues
        self._api_problem_objects = {
            'DataStatistics',  # Known to cause API timeouts
            'FieldHistoryArchive',  # Very large, causes memory issues
        }
    
    def is_cache_valid(self, object_name: str) -> bool:
        """Check if cached result is still valid."""
        if object_name not in self._cache_timestamps:
            return False
        
        cache_time = self._cache_timestamps[object_name]
        return datetime.now() - cache_time < self.cache_ttl
    
    def clear_cache(self, object_name: Optional[str] = None):
        """Clear cache for specific object or all objects."""
        if object_name:
            self._object_cache.pop(object_name, None)
            self._cache_timestamps.pop(object_name, None)
        else:
            self._object_cache.clear()
            self._cache_timestamps.clear()
            self._global_describe_cache = None
            self._global_describe_timestamp = None
        
        logger.info(f"🗑️ Cleared cache for {'all objects' if not object_name else object_name}")
    
    def _get_global_describe(self) -> Dict[str, Any]:
        """Get global describe with caching."""
        now = datetime.now()
        
        # Check if global describe cache is valid
        if (self._global_describe_cache and self._global_describe_timestamp and
            now - self._global_describe_timestamp < self.cache_ttl):
            return self._global_describe_cache
        
        try:
            logger.info("🔄 Fetching global describe from Salesforce API...")
            describe_result = self.sf_client.describe()
            
            self._global_describe_cache = describe_result
            self._global_describe_timestamp = now
            
            logger.info(f"✅ Global describe cached - {len(describe_result.get('sobjects', []))} objects found")
            return describe_result
            
        except Exception as e:
            logger.error(f"❌ Failed to get global describe: {e}")
            if self._global_describe_cache:
                logger.warning("🔄 Using stale global describe cache")
                return self._global_describe_cache
            raise
    
    def check_object_queryability_fast(self, object_name: str) -> Tuple[bool, str]:
        """
        Fast check using global describe (batch approach).
        
        Returns:
            Tuple[bool, str]: (is_queryable, reason)
        """
        try:
            # Check API problem objects first
            if object_name in self._api_problem_objects:
                return False, f"Object '{object_name}' known to cause API issues"
            
            # Check cache first
            if self.is_cache_valid(object_name):
                cached_result = self._object_cache[object_name]
                logger.debug(f"📋 Cache hit for {object_name}: {cached_result}")
                return cached_result
            
            # Get global describe
            global_describe = self._get_global_describe()
            sobjects = global_describe.get('sobjects', [])
            
            # Find the object in global describe
            for sobject in sobjects:
                if sobject.get('name') == object_name:
                    is_queryable = sobject.get('queryable', False)
                    is_retrieveable = sobject.get('retrieveable', False)
                    is_deprecated = sobject.get('deprecatedAndHidden', False)
                    
                    # Determine if object is usable
                    if is_deprecated:
                        result = (False, f"Object '{object_name}' is deprecated and hidden")
                    elif not is_queryable:
                        result = (False, f"Object '{object_name}' is not queryable")
                    elif not is_retrieveable:
                        result = (False, f"Object '{object_name}' is not retrieveable")
                    else:
                        result = (True, f"Object '{object_name}' is queryable")
                    
                    # Cache the result
                    self._object_cache[object_name] = result
                    self._cache_timestamps[object_name] = datetime.now()
                    
                    logger.debug(f"✅ {result[1]}")
                    return result
            
            # Object not found in global describe
            result = (False, f"Object '{object_name}' not found in Salesforce org")
            self._object_cache[object_name] = result
            self._cache_timestamps[object_name] = datetime.now()
            
            logger.warning(f"⚠️ {result[1]}")
            return result
            
        except Exception as e:
            logger.error(f"❌ Error checking {object_name} via global describe: {e}")
            # Fallback to individual describe
            return self.check_object_queryability_individual(object_name)
    
    def check_object_queryability_individual(self, object_name: str) -> Tuple[bool, str]:
        """
        Detailed check using individual object describe.
        
        Returns:
            Tuple[bool, str]: (is_queryable, reason)
        """
        try:
            # Check cache first
            if self.is_cache_valid(object_name):
                cached_result = self._object_cache[object_name]
                logger.debug(f"📋 Cache hit for {object_name}: {cached_result}")
                return cached_result
            
            logger.debug(f"🔍 Individual describe for {object_name}")
            
            # Get individual object describe
            describe_result = self.sf_client.__getattr__(object_name).describe()
            
            # Check various queryability factors
            is_queryable = describe_result.get('queryable', False)
            is_retrieveable = describe_result.get('retrieveable', False)
            is_deprecated = describe_result.get('deprecatedAndHidden', False)
            is_custom = describe_result.get('custom', False)
            
            # Additional checks for edge cases
            fields = describe_result.get('fields', [])
            has_id_field = any(field.get('name') == 'Id' for field in fields)
            
            # Determine result
            if is_deprecated:
                result = (False, f"Object '{object_name}' is deprecated and hidden")
            elif not is_queryable:
                result = (False, f"Object '{object_name}' is not queryable")
            elif not is_retrieveable:
                result = (False, f"Object '{object_name}' is not retrieveable")
            elif not has_id_field and not is_custom:
                result = (False, f"Object '{object_name}' has no Id field")
            else:
                result = (True, f"Object '{object_name}' is queryable")
            
            # Cache the result
            self._object_cache[object_name] = result
            self._cache_timestamps[object_name] = datetime.now()
            
            logger.debug(f"✅ {result[1]}")
            return result
            
        except Exception as e:
            logger.warning(f"⚠️ Individual describe failed for {object_name}: {e}")
            # Try test query as last resort
            return self.check_object_queryability_test_query(object_name)
    
    def check_object_queryability_test_query(self, object_name: str) -> Tuple[bool, str]:
        """
        Last resort: try a test query to check queryability.
        
        Returns:
            Tuple[bool, str]: (is_queryable, reason)
        """
        try:
            logger.debug(f"🧪 Test query for {object_name}")
            
            # Try a minimal query
            test_query = f"SELECT Id FROM {object_name} LIMIT 1"
            self.sf_client.query(test_query)
            
            result = (True, f"Object '{object_name}' passed test query")
            
            # Cache the result
            self._object_cache[object_name] = result
            self._cache_timestamps[object_name] = datetime.now()
            
            logger.debug(f"✅ {result[1]}")
            return result
            
        except Exception as e:
            error_msg = str(e).lower()
            
            # Parse common error messages to determine the reason
            if 'invalid object name' in error_msg or 'does not exist' in error_msg:
                reason = f"Object '{object_name}' does not exist in this Salesforce org"
            elif 'not supported' in error_msg or 'cannot be queried' in error_msg:
                reason = f"Object '{object_name}' does not support query operations"
            elif 'insufficient access' in error_msg or 'no access' in error_msg:
                reason = f"Object '{object_name}' - insufficient permissions for querying"
            else:
                reason = f"Object '{object_name}' query failed: {e}"
            
            result = (False, reason)
            
            # Cache the result
            self._object_cache[object_name] = result
            self._cache_timestamps[object_name] = datetime.now()
            
            logger.debug(f"❌ {reason}")
            return result


# Global checker instance
_global_checker: Optional[SalesforceObjectChecker] = None

def initialize_dynamic_checker(sf_client, cache_ttl_minutes: int = 60) -> SalesforceObjectChecker:
    """Initialize the global dynamic checker instance."""
    global _global_checker
    _global_checker = SalesforceObjectChecker(sf_client, cache_ttl_minutes)
    logger.info("🚀 Initialized dynamic Salesforce object checker")
    return _global_checker

def get_dynamic_checker() -> Optional[SalesforceObjectChecker]:
    """Get the global dynamic checker instance."""
    return _global_checker

def is_salesforce_object_queryable(object_name: str, sf_client=None) -> bool:
    """
    Check if a Salesforce object is queryable using dynamic API calls.
    
    This is the main function that replaces the old hardcoded approach.
    
    Args:
        object_name (str): The Salesforce object name to check
        sf_client: Optional Salesforce client (uses global if not provided)
        
    Returns:
        bool: True if the object is queryable, False otherwise
        
    Example:
        >>> is_salesforce_object_queryable('Account')
        True
        >>> is_salesforce_object_queryable('Account__ChangeEvent')
        False
    """
    if not object_name or not isinstance(object_name, str):
        logger.warning(f"⚠️ Invalid object name: {object_name}")
        return False
    
    # Get or create checker instance
    checker = _global_checker
    if not checker and sf_client:
        checker = initialize_dynamic_checker(sf_client)
    elif not checker:
        logger.error("❌ No Salesforce client available for dynamic checking")
        return False
    
    try:
        # Use fast method (global describe) first
        is_queryable, reason = checker.check_object_queryability_fast(object_name)
        
        if is_queryable:
            logger.debug(f"✅ {object_name}: {reason}")
        else:
            logger.info(f"🚫 {object_name}: {reason}")
        
        return is_queryable
        
    except Exception as e:
        logger.error(f"❌ Dynamic check failed for {object_name}: {e}")
        # Fail safe - assume not queryable if we can't determine
        return False

def batch_check_object_queryability(object_names: list, sf_client=None) -> Dict[str, bool]:
    """
    Check multiple objects in batch for better performance.
    
    Args:
        object_names (list): List of Salesforce object names
        sf_client: Optional Salesforce client
        
    Returns:
        Dict[str, bool]: Mapping of object name to queryability
    """
    if not object_names:
        return {}
    
    checker = _global_checker
    if not checker and sf_client:
        checker = initialize_dynamic_checker(sf_client)
    elif not checker:
        logger.error("❌ No Salesforce client available for batch checking")
        return {obj: False for obj in object_names}
    
    results = {}
    
    try:
        # Pre-load global describe for efficiency
        checker._get_global_describe()
        
        # Check each object
        for obj_name in object_names:
            results[obj_name] = is_salesforce_object_queryable(obj_name, sf_client)
        
        logger.info(f"📊 Batch check completed: {sum(results.values())}/{len(results)} objects queryable")
        return results
        
    except Exception as e:
        logger.error(f"❌ Batch check failed: {e}")
        return {obj: False for obj in object_names}

def refresh_object_cache(object_name: Optional[str] = None):
    """Refresh cache for specific object or all objects."""
    checker = get_dynamic_checker()
    if checker:
        checker.clear_cache(object_name)
        logger.info(f"🔄 Refreshed cache for {'all objects' if not object_name else object_name}")
    else:
        logger.warning("⚠️ No dynamic checker initialized")

def get_cache_stats() -> Dict[str, Any]:
    """Get statistics about the current cache."""
    checker = get_dynamic_checker()
    if not checker:
        return {}
    
    return {
        'cached_objects': len(checker._object_cache),
        'cache_hits': sum(1 for obj in checker._object_cache.keys() if checker.is_cache_valid(obj)),
        'cache_ttl_minutes': checker.cache_ttl.total_seconds() / 60,
        'global_describe_cached': checker._global_describe_cache is not None
    }
