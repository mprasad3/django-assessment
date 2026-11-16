"""
Root URL configuration.
"""
from django.contrib import admin
from django.http import HttpResponse
from django.urls import path, include


def home_view(request):
    html = """
    <!DOCTYPE html>
    <html>
    <head>
        <title>Django Assessment Dashboard</title>
        <style>
            body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; max-width: 700px; margin: 40px auto; padding: 20px; line-height: 1.6; }
            h1 { color: #1a1a1a; }
            .card { background: #f8f9fa; border: 1px solid #e9ecef; border-radius: 8px; padding: 18px; margin-bottom: 16px; }
            a { color: #0969da; text-decoration: none; font-weight: 600; font-size: 1.1em; }
            a:hover { text-decoration: underline; }
            .badge-success { background: #dafbe1; color: #1a7f37; padding: 3px 8px; border-radius: 12px; font-size: 0.8em; }
            .badge-danger { background: #ffebe9; color: #cf222e; padding: 3px 8px; border-radius: 12px; font-size: 0.8em; }
            .badge-info { background: #ddf4ff; color: #0969da; padding: 3px 8px; border-radius: 12px; font-size: 0.8em; }
            p { margin: 6px 0 0; color: #57606a; }
        </style>
    </head>
    <body>
        <h1>Technical Assessment - Local Dashboard</h1>
        <p>Endpoints available for local testing and inspection:</p>
        <br/>
        <div class="card">
            <a href="/api/orders/summary/">/api/orders/summary/</a>
            <span class="badge-success">Fixed View (3 queries)</span>
            <p>Optimized with select_related and prefetch_related.</p>
        </div>
        <div class="card">
            <a href="/api/orders/summary/broken/">/api/orders/summary/broken/</a>
            <span class="badge-danger">Broken View (1,250+ queries)</span>
            <p>N+1 regression scenario reproducing the timeout on 200+ orders.</p>
        </div>
        <div class="card">
            <a href="/silk/">/silk/</a>
            <span class="badge-info">Silk Profiler</span>
            <p>Silk request inspection dashboard.</p>
        </div>
    </body>
    </html>
    """
    return HttpResponse(html)


urlpatterns = [
    path("", home_view, name="home"),
    path("admin/", admin.site.urls),
    path("silk/", include("silk.urls", namespace="silk")),
    path("api/", include("apps.orders.urls")),
]
