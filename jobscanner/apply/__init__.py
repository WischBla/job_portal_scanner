"""Playwright-based application assistant. It never submits an application."""

from .assistant import SERVICE, ApplyError, build_report, document_choice
from .adapters import detect

__all__ = ['SERVICE', 'ApplyError', 'build_report', 'document_choice', 'detect']
