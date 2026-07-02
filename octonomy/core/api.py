"""DRF view helpers for the versioned URL layer.

``NamespaceURLPathVersioning`` serves both versions from ``api/<version>/``, so
DRF captures a ``version`` URL kwarg and forwards it to the view callable. The
existing function-based views take fixed positional kwargs and would ``TypeError``
on the extra ``version`` argument. This ``api_view`` wraps DRF's decorator and
strips ``version`` before the view runs (it is still available as
``request.version``), so views keep their current signatures.
"""

from __future__ import annotations

import functools

from rest_framework.decorators import api_view as drf_api_view


def api_view(http_method_names=None):
    def decorator(func):
        @functools.wraps(func)
        def handler(request, *args, **kwargs):
            kwargs.pop("version", None)
            return func(request, *args, **kwargs)

        # drf_api_view builds the WrappedAPIView and exposes it as ``.cls``, which
        # require_scopes and drf-spectacular rely on; keep that structure intact.
        return drf_api_view(http_method_names)(handler)

    return decorator
