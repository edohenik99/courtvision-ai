from courtvision.sports.mlb.player_name_normalization import normalize_mlb_player_name


def test_mlb_player_name_normalizer_strips_suffixes() -> None:
    assert normalize_mlb_player_name("Rafael Flores Jr.") == "rafael flores"
    assert normalize_mlb_player_name("Example Hitter Sr") == "example hitter"
    assert normalize_mlb_player_name("Example Hitter II") == "example hitter"
    assert normalize_mlb_player_name("Example Hitter III") == "example hitter"
    assert normalize_mlb_player_name("Example Hitter IV") == "example hitter"


def test_mlb_player_name_normalizer_canonicalizes_nicknames() -> None:
    assert normalize_mlb_player_name("Josh Kuroda-Grauer") == (
        normalize_mlb_player_name("Joshua Kuroda-Grauer")
    )
    assert normalize_mlb_player_name("Cam Cauley") == (
        normalize_mlb_player_name("Cameron Cauley")
    )
    assert normalize_mlb_player_name("Mike Trout") == "michael trout"


def test_mlb_player_name_normalizer_normalizes_punctuation_and_accents() -> None:
    assert normalize_mlb_player_name("J.P. Crawford") == (
        normalize_mlb_player_name("JP Crawford")
    )
    assert normalize_mlb_player_name("Tyler O'Neill") == (
        normalize_mlb_player_name("Tyler ONeill")
    )
    assert normalize_mlb_player_name("Jos\u00e9 Ram\u00edrez") == "jose ramirez"
