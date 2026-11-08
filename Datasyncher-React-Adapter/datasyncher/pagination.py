from rest_framework.pagination import PageNumberPagination
from rest_framework.response import Response

class CustomPagination(PageNumberPagination):
    def get_paginated_response(self, data):
        return Response({
            'content': data,
            'totalElements': self.page.paginator.count,
            'size': self.get_page_size(self.request)
        })
