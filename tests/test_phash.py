import numpy as np
import pytest

from gamepal.perception.phash import HASH_BITS, dhash, distance
from tests.conftest import pattern, solid


def test_identical_images_have_zero_distance() -> None:
    image = pattern(3)
    assert distance(dhash(image), dhash(image.copy())) == 0


def test_small_noise_stays_below_threshold() -> None:
    image = pattern(3, 640, 360)
    noise = np.random.default_rng(9).integers(-4, 5, image.shape)
    noisy = np.clip(image.astype(int) + noise, 0, 255).astype(np.uint8)
    assert distance(dhash(image), dhash(noisy)) < 12


def test_different_screens_are_far_apart() -> None:
    assert distance(dhash(pattern(1)), dhash(pattern(2))) > 60


def test_hash_fits_in_256_bits() -> None:
    assert dhash(pattern(5)).bit_length() <= HASH_BITS
    assert dhash(solid((10, 10, 10))) == 0


def test_rejects_non_rgb() -> None:
    with pytest.raises(ValueError, match="RGB"):
        dhash(np.zeros((10, 10), dtype=np.uint8))
