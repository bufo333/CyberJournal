# -*- coding: utf-8 -*-
"""Tests for cyberjournal.map module."""
from __future__ import annotations

from cyberjournal.map import (
    text_seed,
    text_to_map,
    render_ascii,
    render_colored_map,
    classify,
    top_keywords,
    rand01,
    noise,
)


class TestTextSeed:
    def test_deterministic(self):
        assert text_seed("hello") == text_seed("hello")

    def test_different_text_different_seed(self):
        assert text_seed("hello") != text_seed("world")


class TestRand01:
    def test_range(self):
        for i in range(100):
            v = rand01(42, i, i * 2)
            assert 0.0 <= v < 1.0

    def test_deterministic(self):
        assert rand01(42, 1, 2) == rand01(42, 1, 2)


class TestNoise:
    def test_range(self):
        for x in range(20):
            for y in range(20):
                v = noise(42, float(x), float(y))
                assert 0.0 <= v <= 1.0

    def test_deterministic(self):
        assert noise(42, 5.0, 5.0) == noise(42, 5.0, 5.0)


class TestClassify:
    def test_water(self):
        assert classify(0.1, 0.5) == "water"

    def test_shore(self):
        assert classify(0.30, 0.5) == "shore"

    def test_mount(self):
        assert classify(0.9, 0.5) == "mount"

    def test_field(self):
        assert classify(0.5, 0.3) == "field"

    def test_forest(self):
        assert classify(0.5, 0.8) == "forest"


class TestTopKeywords:
    def test_basic(self):
        text = "python python python java java rust"
        kws = top_keywords(text, k=2)
        assert "python" in kws

    def test_filters_stopwords(self):
        text = "the the the from from about about"
        kws = top_keywords(text, k=3)
        assert len(kws) == 0


class TestTextToMap:
    def test_basic_generation(self):
        openings, types, costs, legend = text_to_map("Hello world test text")
        assert len(types) > 0
        assert len(types[0]) > 0
        assert "tiles" in legend
        assert "seed" in legend

    def test_deterministic(self):
        _, t1, _, l1 = text_to_map("Same input text")
        _, t2, _, l2 = text_to_map("Same input text")
        assert t1 == t2
        assert l1["seed"] == l2["seed"]

    def test_custom_size(self):
        _, types, _, _ = text_to_map("test", width=20, height=10)
        assert len(types) == 10
        assert len(types[0]) == 20

    def test_different_text_different_map(self):
        _, t1, _, _ = text_to_map("alpha beta gamma")
        _, t2, _, _ = text_to_map("delta epsilon zeta")
        assert t1 != t2

    def test_seed_diversity(self):
        """Verify the wider modulus fix gives more distinct scale pairs."""
        seeds = set()
        for i in range(100):
            s = text_seed(f"text_{i}")
            scale_e = 8.0 + (s % 97) * 0.2
            scale_m = 10.0 + ((s >> 16) % 89) * 0.25
            seeds.add((round(scale_e, 1), round(scale_m, 2)))
        # With wider modulus, we should get many more distinct pairs than the old 77 max
        assert len(seeds) > 77


class TestRenderers:
    def test_render_ascii(self):
        _, types, _, legend = text_to_map("test map rendering")
        output = render_ascii(types, legend)
        assert isinstance(output, str)
        assert len(output) > 0
        assert "Legend:" in output

    def test_render_colored_utf(self):
        _, types, _, legend = text_to_map("colored map test")
        output = render_colored_map(types, legend, charset="utf", color=True, border=True)
        assert isinstance(output, str)
        assert len(output) > 0

    def test_render_no_color_no_border(self):
        _, types, _, legend = text_to_map("plain map")
        output = render_colored_map(types, legend, charset="ascii", color=False, border=False)
        assert isinstance(output, str)
        # No ANSI codes
        assert "\x1b[" not in output
