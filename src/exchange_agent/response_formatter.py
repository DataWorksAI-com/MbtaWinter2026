"""
Response Formatter for MBTA Agntcy
Sanitizes and formats responses for consistent user experience
"""
import re
import logging
from typing import Dict, Any, Optional

logger = logging.getLogger(__name__)


class ResponseFormatter:
    """
    Formats and sanitizes responses from agents and LLM
    """
    
    def __init__(self):
        self.max_response_length = 1000  # Character limit
        
    def format_response(
        self,
        response: str,
        intent: str,
        metadata: Optional[Dict[str, Any]] = None
    ) -> str:
        """
        Main formatting pipeline
        
        Args:
            response: Raw response text
            intent: Detected intent
            metadata: Additional context (agents used, etc.)
        
        Returns:
            Formatted, sanitized response
        """
        if not response or not response.strip():
            return self._get_fallback_response(intent)
        
        # Step 1: Clean up the text
        cleaned = self._sanitize_text(response)
        
        # Step 2: Remove technical artifacts
        cleaned = self._remove_artifacts(cleaned)
        
        # Step 3: Format based on intent
        formatted = self._format_by_intent(cleaned, intent, metadata)
        
        # Step 4: Ensure length limits
        formatted = self._enforce_length_limit(formatted)
        
        # Step 5: Add contextual enhancements
        formatted = self._add_enhancements(formatted, intent, metadata)
        
        return formatted.strip()
    
    def _sanitize_text(self, text: str) -> str:
        """Remove unwanted characters and normalize whitespace"""
        # Remove multiple spaces
        text = re.sub(r'\s+', ' ', text)
        
        # Remove multiple newlines (keep max 2)
        text = re.sub(r'\n{3,}', '\n\n', text)
        
        # Remove markdown artifacts if present
        text = re.sub(r'\*\*\*+', '', text)  # Multiple asterisks
        
        # Remove HTML tags (just in case)
        text = re.sub(r'<[^>]+>', '', text)
        
        # Fix punctuation spacing
        text = re.sub(r'\s+([.,!?;:])', r'\1', text)  # Remove space before punctuation
        text = re.sub(r'([.,!?;:])(\w)', r'\1 \2', text)  # Add space after punctuation
        
        return text.strip()
    
    def _remove_artifacts(self, text: str) -> str:
        """Remove technical artifacts from agent/LLM responses"""
        
        # Remove common artifacts - MORE COMPREHENSIVE PATTERNS
        artifacts_to_remove = [
            r'\[Agent:\s*[\w-]+\]',  # [Agent: mbta-alerts] or [Agent:mbta-alerts]
            r'\[Intent:\s*\w+\]',  # [Intent: alerts]
            r'\[Confidence:\s*[\d.\s]+\]',  # [Confidence: 0.95] or [Confidence: 0. 95]
            r'INTENT:\s*\w+',  # INTENT: alerts
            r'NEEDS_MBTA_DATA:\s*\w+',  # NEEDS_MBTA_DATA: yes
            r'ENTITIES:\s*\[.*?\]',  # ENTITIES: [...]
            r'RESPONSE:\s*',  # RESPONSE:
            r'\[[\w\s]+:\s*[\d.\s]+\]',  # Generic [Key: value] patterns
        ]
        
        for pattern in artifacts_to_remove:
            text = re.sub(pattern, '', text, flags=re.DOTALL | re.IGNORECASE)
        
        # Remove leading/trailing quotes
        text = text.strip('"\'')
        
        # Clean up extra spaces left by removal
        text = re.sub(r'\s+', ' ', text)
        
        return text.strip()
    
    def _format_by_intent(
        self,
        text: str,
        intent: str,
        metadata: Optional[Dict[str, Any]] = None
    ) -> str:
        """Format response based on intent type"""
        
        if intent == 'alerts':
            return self._format_alerts_response(text, metadata)
        elif intent == 'trip_planning':
            return self._format_trip_planning_response(text, metadata)
        elif intent == 'stop_info':
            return self._format_stop_info_response(text, metadata)
        elif intent == 'schedule':
            return self._format_schedule_response(text, metadata)
        else:
            return text  # General responses don't need special formatting
    
    def _format_alerts_response(self, text: str, metadata: Optional[Dict] = None) -> str:
        """Format alert responses with emoji and clear structure"""
        
        # Check if there are actual alerts
        has_issues = any(keyword in text.lower() for keyword in [
            'delay', 'disruption', 'issue', 'problem', 'suspended',
            'closed', 'cancelled', 'experiencing'
        ])
        
        if has_issues:
            # Add warning emoji for issues
            if not text.startswith('⚠️'):
                text = f"⚠️ {text}"
        else:
            # Add checkmark for all clear
            if not text.startswith('✅'):
                text = f"✅ {text}"
        
        return text
    
    def _format_trip_planning_response(self, text: str, metadata: Optional[Dict] = None) -> str:
        """Format trip planning with clear directions"""
        
        # Add route emoji if not present
        if not any(emoji in text for emoji in ['🚇', '🚉', '🚌', '🚊']):
            text = f"🚇 {text}"
        
        return text
    
    def _format_stop_info_response(self, text: str, metadata: Optional[Dict] = None) -> str:
        """Format stop information clearly"""
        
        # Add location emoji
        if not text.startswith('📍'):
            text = f"📍 {text}"
        
        return text
    
    def _format_schedule_response(self, text: str, metadata: Optional[Dict] = None) -> str:
        """Format schedule information"""
        
        # Add clock emoji
        if not text.startswith('⏰'):
            text = f"⏰ {text}"
        
        return text
    
    def _enforce_length_limit(self, text: str) -> str:
        """Ensure response doesn't exceed length limit"""
        
        if len(text) <= self.max_response_length:
            return text
        
        # Truncate at sentence boundary if possible
        truncated = text[:self.max_response_length]
        
        # Try to end at last complete sentence
        last_period = truncated.rfind('.')
        last_exclamation = truncated.rfind('!')
        last_question = truncated.rfind('?')
        
        last_sentence_end = max(last_period, last_exclamation, last_question)
        
        if last_sentence_end > self.max_response_length * 0.7:  # At least 70% of limit
            truncated = truncated[:last_sentence_end + 1]
        else:
            truncated += '...'
        
        logger.warning(f"Response truncated from {len(text)} to {len(truncated)} characters")
        return truncated
    
    def _add_enhancements(
        self,
        text: str,
        intent: str,
        metadata: Optional[Dict[str, Any]] = None
    ) -> str:
        """Add helpful enhancements based on context"""
        
        if not metadata:
            return text
        
        enhancements = []
        
        # Add follow-up suggestions for certain intents
        if intent == 'alerts' and 'delay' in text.lower():
            enhancements.append("\n\n💡 Tip: I can also help you find alternative routes!")
        
        if intent == 'stop_info':
            enhancements.append("\n\n💡 Need directions? Just ask how to get there!")
        
        # Only add enhancements if text isn't too long
        if len(text) < self.max_response_length * 0.8:
            text += ''.join(enhancements)
        
        return text
    
    def _get_fallback_response(self, intent: str) -> str:
        """Provide fallback response when main response is empty"""
        
        fallbacks = {
            'alerts': "I'm checking the current service status for you, but I couldn't retrieve the information right now. Please try again in a moment.",
            'trip_planning': "I'd be happy to help you plan your trip! Could you provide more details about your starting point and destination?",
            'stop_info': "I can help you find stops and stations. Which area are you interested in?",
            'schedule': "I can check arrival times for you. Which station and line are you interested in?",
            'general': "How can I assist you with the MBTA today?"
        }
        
        return fallbacks.get(intent, "I'm here to help! What would you like to know about the MBTA?")
    
    def format_error_response(self, error_type: str = "general") -> str:
        """Format user-friendly error messages"""
        
        error_messages = {
            'agent_timeout': "I'm having trouble reaching the MBTA systems right now. Please try again in a moment. 🔄",
            'agent_error': "I encountered an issue while checking that information. Could you try rephrasing your question? 🤔",
            'no_data': "I couldn't find any information for that query. Could you provide more details? 📝",
            'general': "Something went wrong on my end. Please try again! 🔧"
        }
        
        return error_messages.get(error_type, error_messages['general'])


# Singleton instance
_formatter_instance = None

def get_response_formatter() -> ResponseFormatter:
    """Get or create the response formatter singleton"""
    global _formatter_instance
    if _formatter_instance is None:
        _formatter_instance = ResponseFormatter()
    return _formatter_instance