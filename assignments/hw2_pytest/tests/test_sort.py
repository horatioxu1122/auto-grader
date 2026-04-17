"""Test cases for hw2 sorting assignment."""

from solution import sort_list


def test_ascending():
    assert sort_list([3, 1, 2]) == [1, 2, 3]


def test_descending():
    assert sort_list([3, 1, 2], reverse=True) == [3, 2, 1]


def test_empty():
    assert sort_list([]) == []


def test_single_element():
    assert sort_list([42]) == [42]


def test_duplicates():
    assert sort_list([3, 1, 3, 2, 1]) == [1, 1, 2, 3, 3]
