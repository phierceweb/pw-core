"""Exception raised by the Anthropic client."""

from pw_foundation.exceptions import ClientError


class AnthropicError(ClientError):
    """Anthropic API call failed (SDK error, network failure, or bad config)."""


AnthropicError.__module__ = "pw_core.clients.anthropic"  # public path for tracebacks and pickles
