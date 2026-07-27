"""Fixture calculator module for Engineer boundary verification."""


def add(a, b):
    return a + b


def total_pages(item_count, page_size):
    return (item_count + page_size - 1) // page_size
