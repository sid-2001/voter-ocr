from scripts.parser_utils import parse_card_text, parse_metadata


def test_parse_card_text_sample_entry():
    text = """
    184 AJP2228518
    Name : VIKAS SURPAM
    Fathers Name: MANKU SURPAM
    House Number : 1-17/1
    Age : 22 Gender : Male
    """

    parsed = parse_card_text(text)

    assert parsed["serial_no"] == "184"
    assert parsed["epic_no"] == "AJP2228518"
    assert parsed["voter_name"] == "VIKAS SURPAM"
    assert parsed["relation_type"] == "Father"
    assert parsed["relation_name"] == "MANKU SURPAM"
    assert parsed["house_number"] == "1-17/1"
    assert parsed["age"] == 22
    assert parsed["gender"] == "Male"


def test_parse_card_text_husband_and_female():
    text = """
    201 AJP0017129
    Name : Vetti Kavit
    Husbands Name: Chrandas
    House Number : 1-20
    Age : 41 Gender : Female
    """

    parsed = parse_card_text(text)

    assert parsed["serial_no"] == "201"
    assert parsed["relation_type"] == "Husband"
    assert parsed["relation_name"] == "Chrandas"
    assert parsed["gender"] == "Female"


def test_parse_metadata_header():
    text = """
    Assembly Constituency No and Name : 1-SIRPUR     Part No. : 1
    Section No and Name 1-Malini
    """

    metadata = parse_metadata(text)

    assert metadata["constituency_no"] == "1"
    assert metadata["constituency_name"] == "SIRPUR"
    assert metadata["part_no"] == "1"
    assert metadata["section_no"] == "1"
    assert metadata["section_name"] == "Malini"
