"""Catalogue: validation, copy management, rollup discipline and duplicate detection.

The invariant this file defends hardest is that ``books.copies`` is a *derived*
number. Every mutation path must leave it equal to the real copy count, because
the availability badge on every screen reads it.
"""

from __future__ import annotations

import unittest

from helpers import ServiceTestCase, rollup_mismatches

from imri import config, errors
from imri.catalog import barcode_for, validate_dewey, validate_isbn

# A real ISBN-13 with a correct check digit, and one with the last digit wrong.
GOOD_ISBN = "9780306406157"
BAD_ISBN = "9780306406150"


def code_of(callable_, *args, **kwargs) -> str:
    try:
        callable_(*args, **kwargs)
    except errors.ImriError as exc:
        return exc.code
    raise AssertionError("expected an ImriError, but the call succeeded")


class IsbnTests(unittest.TestCase):
    def test_valid_isbn13(self) -> None:
        self.assertTrue(validate_isbn(GOOD_ISBN))

    def test_bad_check_digit_is_rejected(self) -> None:
        self.assertFalse(validate_isbn(BAD_ISBN))

    def test_hyphens_and_spaces_are_tolerated(self) -> None:
        self.assertTrue(validate_isbn("978-0-306-40615-7"))
        self.assertTrue(validate_isbn(" 978 0 306 40615 7 "))

    def test_isbn10_is_accepted(self) -> None:
        self.assertTrue(validate_isbn("0306406152"))

    def test_empty_is_valid_because_isbn_is_optional(self) -> None:
        self.assertTrue(validate_isbn(""))
        self.assertTrue(validate_isbn(None))

    def test_letters_are_rejected(self) -> None:
        self.assertFalse(validate_isbn("978030640615X"))


class DeweyTests(unittest.TestCase):
    def test_whole_numbers(self) -> None:
        for value in ("000", "500", "899", "999"):
            self.assertTrue(validate_dewey(value), value)

    def test_decimals(self) -> None:
        for value in ("823.914", "005.133", "499.23"):
            self.assertTrue(validate_dewey(value), value)

    def test_malformed_values(self) -> None:
        for value in ("8234", "abc", "823.", "-823", "8a3"):
            self.assertFalse(validate_dewey(value), value)

    def test_empty_is_allowed(self) -> None:
        self.assertTrue(validate_dewey(None))
        self.assertTrue(validate_dewey(""))


class CreateBookTests(ServiceTestCase):
    def test_minimal_book_gets_accession_barcode_and_one_copy(self) -> None:
        result = self.make_book(title="Dewan Masyarakat")
        book = result["book"]
        self.assertEqual(result["copies_created"], 1)
        self.assertTrue(book["accession_no"])
        self.assertTrue(book["barcode"])
        self.assertEqual(book["status"], "available")
        self.assertEqual(book["copies"], 1)

        copies = self.copies_of(book["id"])
        self.assertEqual(len(copies), 1)
        self.assertEqual(copies[0]["barcode"], barcode_for(book["accession_no"], 1))
        self.assertEqual(copies[0]["status"], "available")

    def test_title_is_mandatory(self) -> None:
        self.assertEqual(
            code_of(self.catalog.create_book, {"title": "   ", "copies": 1}),
            errors.TITLE_REQUIRED,
        )

    def test_duplicate_isbn_is_refused(self) -> None:
        self.make_book(title="First", isbn=GOOD_ISBN)
        self.assertEqual(
            code_of(self.catalog.create_book, {"title": "Second", "isbn": GOOD_ISBN}),
            errors.DUPLICATE_ISBN,
        )

    def test_duplicate_accession_is_refused(self) -> None:
        self.make_book(title="First", accession_no="ACC-0001")
        self.assertEqual(
            code_of(
                self.catalog.create_book,
                {"title": "Second", "accession_no": "ACC-0001"},
            ),
            errors.DUPLICATE_ACCESSION,
        )

    def test_invalid_isbn_is_refused(self) -> None:
        self.assertEqual(
            code_of(self.catalog.create_book, {"title": "Bad", "isbn": BAD_ISBN}),
            errors.INVALID_ISBN,
        )

    def test_unknown_material_type_is_refused(self) -> None:
        self.assertEqual(
            code_of(
                self.catalog.create_book,
                {"title": "Odd", "material_type": "hologram"},
            ),
            errors.UNKNOWN_MATERIAL_TYPE,
        )

    def test_copy_count_bounds(self) -> None:
        self.assertEqual(
            code_of(self.catalog.create_book, {"title": "Too many", "copies": 501}),
            errors.VALIDATION_FAILED,
        )
        # Zero copies is legal: a title can be catalogued before it arrives.
        result = self.catalog.create_book({"title": "Not yet delivered", "copies": 0})
        self.assertEqual(result["copies_created"], 0)
        self.assertEqual(result["book"]["copies"], 0)
        self.assertEqual(result["book"]["status"], "unavailable")

    def test_subjects_and_languages_are_linked(self) -> None:
        book = self.catalog.create_book(
            {
                "title": "Sastera",
                "subjects": "Malay literature, Poetry",
                "languages": "ms,en",
                "copies": 1,
            }
        )["book"]
        self.assertEqual(
            sorted(s["heading"] for s in self.catalog.book_subjects(book["id"])),
            ["Malay literature", "Poetry"],
        )
        self.assertEqual(sorted(self.catalog.book_languages(book["id"])), ["en", "ms"])

    def test_a_failed_create_writes_nothing(self) -> None:
        before = self.db.one("SELECT COUNT(*) AS n FROM books")["n"]
        code_of(self.catalog.create_book, {"title": "Bad", "isbn": BAD_ISBN})
        self.assertEqual(self.db.one("SELECT COUNT(*) AS n FROM books")["n"], before)
        self.assertEqual(rollup_mismatches(self.db), [])


class CopyTests(ServiceTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.book = self.make_book(copies=2)["book"]

    def test_adding_copies_numbers_them_sequentially(self) -> None:
        created = self.catalog.add_copies(self.book["id"], 3, actor="tester")
        self.assertEqual([c["copy_no"] for c in created], [3, 4, 5])
        copies = self.copies_of(self.book["id"])
        self.assertEqual([c["copy_no"] for c in copies], [1, 2, 3, 4, 5])
        self.assertEqual(len({c["barcode"] for c in copies}), 5)

    def test_rollup_follows_add_and_delete(self) -> None:
        self.catalog.add_copies(self.book["id"], 2, actor="tester")
        book = self.catalog.get_book(self.book["id"])["book"]
        self.assertEqual(book["copies"], 4)
        self.catalog.delete_copy(self.copies_of(self.book["id"])[0]["id"], actor="tester")
        book = self.catalog.get_book(self.book["id"])["book"]
        self.assertEqual(book["copies"], 3)
        self.assertEqual(rollup_mismatches(self.db), [])

    def test_the_last_copy_cannot_be_deleted(self) -> None:
        copy = self.copies_of(self.book["id"])[0]
        self.catalog.delete_copy(copy["id"], actor="tester")
        self.assertEqual(
            code_of(self.catalog.delete_copy, self.copies_of(self.book["id"])[0]["id"]),
            errors.LAST_COPY,
        )

    def test_an_on_loan_copy_cannot_be_deleted(self) -> None:
        member = self.make_member()["member"]
        copy = self.copies_of(self.book["id"])[0]
        self.circulation.issue(copy["barcode"], member["member_code"], actor="tester")
        self.assertEqual(
            code_of(self.catalog.delete_copy, copy["id"]), errors.COPY_ON_LOAN
        )

    def test_available_copies_counts_only_the_shelf(self) -> None:
        member = self.make_member()["member"]
        self.assertEqual(self.catalog.available_copies(self.book["id"]), 2)
        self.circulation.issue(self.copies_of(self.book["id"])[0]["barcode"], member["member_code"])
        self.assertEqual(self.catalog.available_copies(self.book["id"]), 1)

    def test_barcode_lookup_is_exact(self) -> None:
        copy = self.copies_of(self.book["id"])[0]
        found = self.catalog.lookup_barcode(copy["barcode"])
        self.assertEqual(found["copy"]["id"], copy["id"])
        self.assertEqual(
            code_of(self.catalog.lookup_barcode, "NOPE-9"), errors.NOT_FOUND
        )


class DeleteBookTests(ServiceTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.book = self.make_book(copies=1)["book"]
        self.copy = self.copies_of(self.book["id"])[0]

    def test_a_clean_title_is_deleted_with_its_copies(self) -> None:
        self.catalog.delete_book(self.book["id"], actor="tester")
        self.assertEqual(self.db.one("SELECT COUNT(*) AS n FROM books")["n"], 0)
        self.assertEqual(self.db.one("SELECT COUNT(*) AS n FROM book_copies")["n"], 0)

    def test_a_title_on_loan_cannot_be_deleted(self) -> None:
        member = self.make_member()["member"]
        self.circulation.issue(self.copy["barcode"], member["member_code"], actor="tester")
        self.assertEqual(
            code_of(self.catalog.delete_book, self.book["id"]), errors.BOOK_HAS_ACTIVE_LOANS
        )

    def test_a_title_with_a_queue_cannot_be_deleted(self) -> None:
        member = self.make_member()["member"]
        other = self.make_member("Second")["member"]
        self.circulation.issue(self.copy["barcode"], member["member_code"], actor="tester")
        self.circulation.reserve(self.book["id"], other["id"], actor="tester")
        self.assertEqual(
            code_of(self.catalog.delete_book, self.book["id"]), errors.BOOK_HAS_ACTIVE_LOANS
        )

    def test_deleting_a_missing_book_is_not_found(self) -> None:
        self.assertEqual(code_of(self.catalog.delete_book, 999999), errors.NOT_FOUND)


class ListingTests(ServiceTestCase):
    def setUp(self) -> None:
        super().setUp()
        for index in range(25):
            self.make_book(
                title="Title %02d" % index,
                authors="Author %s" % ("Even" if index % 2 == 0 else "Odd"),
                material_type="book" if index % 3 else "reference",
                dewey="%03d" % (index * 10),
                copies=1,
            )

    def test_pagination_metadata_is_consistent(self) -> None:
        page = self.catalog.list_books({}, {"page": 1, "page_size": 10})
        self.assertEqual(len(page["items"]), 10)
        self.assertEqual(page["meta"]["total"], 25)
        self.assertEqual(page["meta"]["pages"], 3)
        last = self.catalog.list_books({}, {"page": 3, "page_size": 10})
        self.assertEqual(len(last["items"]), 5)

    def test_sorting_by_title_and_order(self) -> None:
        asc = self.catalog.list_books({}, {"page_size": 5, "sort": "title", "order": "asc"})
        desc = self.catalog.list_books({}, {"page_size": 5, "sort": "title", "order": "desc"})
        self.assertEqual(asc["items"][0]["title"], "Title 00")
        self.assertEqual(desc["items"][0]["title"], "Title 24")

    def test_filter_by_material_type(self) -> None:
        page = self.catalog.list_books({"material_type": "reference"}, {"page_size": 50})
        self.assertEqual(page["meta"]["total"], 9)  # indices 0,3,6,...,24
        for item in page["items"]:
            self.assertEqual(item["material_type"], "reference")

    def test_search_matches_title_and_author(self) -> None:
        self.assertEqual(self.catalog.list_books({"q": "Title 07"}, {})["meta"]["total"], 1)
        self.assertEqual(self.catalog.list_books({"q": "Even"}, {})["meta"]["total"], 13)

    def test_page_items_carry_copy_aggregates(self) -> None:
        page = self.catalog.list_books({}, {"page_size": 5})
        for item in page["items"]:
            self.assertEqual(item["total_copies"], 1)
            self.assertEqual(item["available"], 1)
            self.assertEqual(item["on_loan"], 0)

    def test_an_unknown_sort_key_falls_back_instead_of_crashing(self) -> None:
        page = self.catalog.list_books({}, {"sort": "'; DROP TABLE books; --"})
        self.assertEqual(page["meta"]["total"], 25)
        self.assertEqual(page["meta"]["sort"], "title")


class DuplicateTests(ServiceTestCase):
    def test_same_isbn_is_reported_as_a_duplicate(self) -> None:
        self.make_book(title="Original", isbn=GOOD_ISBN)
        found = self.catalog.find_duplicates(title="Original", isbn=GOOD_ISBN)
        self.assertTrue(any(item["reason"] == "same_isbn" for item in found))

    def test_a_distinct_title_is_not_a_duplicate(self) -> None:
        self.make_book(title="Unique Thing", copies=1)
        self.assertEqual(self.catalog.find_duplicates(title="Something Else"), [])


class BulkUpdateTests(ServiceTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.ids = [self.make_book(title="Bulk %d" % i, copies=2)["book"]["id"] for i in range(3)]

    def test_location_patch_applies_to_all(self) -> None:
        changed = self.catalog.bulk_update(self.ids, {"location": "Store Room"}, actor="tester")
        self.assertEqual(changed, 3)
        for book_id in self.ids:
            self.assertEqual(self.catalog.get_book(book_id)["book"]["location"], "Store Room")

    def test_withdrawing_moves_the_shelf_copies(self) -> None:
        """A title's status is derived from its copies, so it lands on unavailable."""
        self.catalog.bulk_update(self.ids, {"status": "withdrawn"}, actor="tester")
        for book_id in self.ids:
            self.assertEqual(self.catalog.get_book(book_id)["book"]["status"], "unavailable")
            self.assertEqual(self.catalog.available_copies(book_id), 0)
            statuses = {c["status"] for c in self.copies_of(book_id)}
            self.assertEqual(statuses, {"withdrawn"})
        self.assertEqual(rollup_mismatches(self.db), [])

    def test_an_arbitrary_status_is_refused(self) -> None:
        self.assertEqual(
            code_of(self.catalog.bulk_update, self.ids, {"status": "exploded"}),
            errors.VALIDATION_FAILED,
        )


class IntegrityUnderChurnTests(ServiceTestCase):
    def test_rollup_survives_a_long_mixed_sequence(self) -> None:
        """The rollup is the single most-read derived value; churn must not drift it."""
        book = self.make_book(copies=5)["book"]
        member = self.make_member()["member"]
        for _ in range(4):
            self.catalog.add_copies(book["id"], 2, actor="tester")
        for copy in self.copies_of(book["id"])[:3]:
            self.circulation.issue(copy["barcode"], member["member_code"], actor="tester")
        for copy in self.copies_of(book["id"])[3:6]:
            self.catalog.update_copy(copy["id"], {"status": "damaged"}, actor="tester")
        for copy in self.copies_of(book["id"])[6:8]:
            self.catalog.delete_copy(copy["id"], actor="tester")

        self.assertEqual(rollup_mismatches(self.db), [])
        book_now = self.catalog.get_book(book["id"])["book"]
        self.assertEqual(book_now["copies"], 11)
        self.assertEqual(self.catalog.available_copies(book["id"]), 5)
        self.assertTrue(self.db.integrity_check()["ok"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
