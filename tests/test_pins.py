"""Offline tests for tools/verify_pins.py: this setup's committed pins for NVIDIA's gateway and supervisor."""
import copy
import os
import re
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))
import verify_pins  # noqa: E402


class PinsTest(unittest.TestCase):
    def setUp(self):
        self.pins = verify_pins.load()

    def test_the_committed_pins_are_well_formed(self):
        self.assertEqual(verify_pins.shape_errors(self.pins), [])

    def test_a_tag_instead_of_a_digest_is_refused(self):
        p = copy.deepcopy(self.pins)
        p["gateway"]["image"] = "ghcr.io/nvidia/openshell/gateway:0.1.3"
        self.assertIn("gateway.image is not pinned by digest (ghcr.io/nvidia/openshell/...@sha256:<64 hex>)",
                      verify_pins.shape_errors(p))

    def test_a_short_checksum_is_refused(self):
        p = copy.deepcopy(self.pins)
        p["supervisor"]["binary_sha256"] = "2c5cb2e8"
        self.assertIn("supervisor.binary_sha256 is not 64 lowercase hex", verify_pins.shape_errors(p))

    def test_a_release_url_that_is_not_nvidias_for_the_version_is_refused(self):
        p = copy.deepcopy(self.pins)
        p["release"] = "https://example.com/openshell/v0.1.3"
        self.assertIn("release is not NVIDIA's download URL for this version", verify_pins.shape_errors(p))

    def test_compose_runs_the_pinned_gateway(self):
        # The digest lives in two files; a bump that moves one starts an unpinned gateway.
        with open(os.path.join(ROOT, "deploy", "compose.yml")) as f:
            images = re.findall(r"image:\s*(ghcr\.io/nvidia/openshell/gateway@\S+)", f.read())
        self.assertEqual(images, [self.pins["gateway"]["image"]])


if __name__ == "__main__":
    unittest.main()
