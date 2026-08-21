"""Groundwork Vector OpenGraph & Social Card Generator (1200x630).

Generates clean, human-centric SVG OpenGraph cards for Google Discover,
social media previews, and SERP rich snippets.
"""

from __future__ import annotations

import html


def wrap_text(text: str, max_chars_per_line: int = 36, max_lines: int = 3) -> list[str]:
    """Wraps text into clean, readable lines for SVG rendering."""
    words = text.split()
    lines = []
    current_line = []
    current_length = 0

    for word in words:
        if current_length + len(word) + 1 <= max_chars_per_line:
            current_line.append(word)
            current_length += len(word) + 1
        else:
            if current_line:
                lines.append(" ".join(current_line))
            current_line = [word]
            current_length = len(word)
            if len(lines) >= max_lines - 1:
                break

    if current_line and len(lines) < max_lines:
        lines.append(" ".join(current_line))

    if len(lines) == max_lines and len(words) > sum(len(line.split()) for line in lines):
        lines[-1] = lines[-1].rstrip(".,;:") + "..."

    return lines


def generate_og_svg(title: str, pillar: str, is_digest: bool = False) -> str:
    """Renders a clean, dignified 1200x630 SVG social share card."""
    pillar_clean = pillar.upper()
    wrapped_lines = wrap_text(title, max_chars_per_line=36, max_lines=3)
    type_label = "RESEARCH BRIEF" if is_digest else "PRACTICAL GUIDE"

    start_y = 270
    line_height = 58
    title_svg_lines = ""
    for i, line in enumerate(wrapped_lines):
        y = start_y + (i * line_height)
        title_svg_lines += f'    <text x="80" y="{y}" fill="#FFFFFF" font-family="-apple-system, BlinkMacSystemFont, \'Segoe UI\', Roboto, Georgia, serif" font-size="44" font-weight="bold" letter-spacing="-0.5">{html.escape(line)}</text>\n'

    return f"""<svg width="1200" height="630" viewBox="0 0 1200 630" fill="none" xmlns="http://www.w3.org/2000/svg">
  <defs>
    <linearGradient id="bgGrad" x1="0%" y1="0%" x2="100%" y2="100%">
      <stop offset="0%" stop-color="#0A192F" />
      <stop offset="60%" stop-color="#112240" />
      <stop offset="100%" stop-color="#071322" />
    </linearGradient>
    <filter id="glow" x="-20%" y="-20%" width="140%" height="140%">
      <feGaussianBlur stdDeviation="60" result="blur" />
      <feComposite in="SourceGraphic" in2="blur" operator="over" />
    </filter>
  </defs>

  <!-- Background -->
  <rect width="1200" height="630" fill="url(#bgGrad)" />

  <!-- Subtle Accent Glow -->
  <circle cx="1050" cy="150" r="200" fill="#10B981" opacity="0.10" filter="url(#glow)" />
  <circle cx="150" cy="550" r="160" fill="#3B82F6" opacity="0.06" filter="url(#glow)" />

  <!-- Outer Border -->
  <rect x="24" y="24" width="1152" height="582" rx="16" stroke="rgba(255, 255, 255, 0.08)" stroke-width="1.5" fill="none" />

  <!-- Brand Header -->
  <g transform="translate(80, 80)">
    <rect width="42" height="42" rx="10" fill="#10B981" />
    <path d="M21 11C15.48 11 11 15.48 11 21C11 26.52 15.48 31 21 31C25.89 31 29.96 27.5 30.87 22.88H21V19.03H34.54C34.63 19.67 34.72 20.31 34.72 21C34.72 28.57 28.57 34.72 21 34.72C13.43 34.72 7.28 28.57 7.28 21C7.28 13.43 13.43 7.28 21 7.28C25.85 7.28 30.04 9.74 32.42 13.48L29.13 15.67C27.39 12.75 24.38 11 21 11Z" fill="#0A192F" />
    
    <text x="56" y="30" fill="#FFFFFF" font-family="-apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif" font-size="24" font-weight="800" letter-spacing="-0.5">GROUNDWORK</text>
  </g>

  <!-- Category Pill -->
  <g transform="translate(80, 160)">
    <rect x="0" y="0" width="110" height="32" rx="6" fill="rgba(16, 185, 129, 0.15)" stroke="rgba(16, 185, 129, 0.3)" />
    <text x="55" y="21" text-anchor="middle" fill="#10B981" font-family="-apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif" font-size="12" font-weight="700" letter-spacing="0.5">{pillar_clean}</text>

    <text x="130" y="21" fill="#8892B0" font-family="-apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif" font-size="13" font-weight="500">• {type_label}</text>
  </g>

  <!-- Headline -->
  <g>
{title_svg_lines}  </g>

  <!-- Footer Tagline -->
  <g transform="translate(80, 530)">
    <text x="0" y="0" fill="#8892B0" font-family="-apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif" font-size="16">Clear, evidence-backed guides for smarter life decisions • gworky.com</text>
  </g>
</svg>
"""
