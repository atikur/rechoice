"""Response settings for the private deployment stage."""
from django.conf import settings


class SearchIndexingMiddleware:
    """Ask crawlers to leave the private-stage site out of search results."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        if settings.SITE_NOINDEX:
            response["X-Robots-Tag"] = "noindex, nofollow"
        return response
