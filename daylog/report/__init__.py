"""Turns stored activity, commits, and meetings into a Report, and renders
that Report as Markdown (and JSON). builder.py does the aggregation once;
render.py and the future JSON API (Phase 6) both consume the same Report
object rather than recomputing anything.
"""
