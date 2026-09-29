"""Reject plausible contract regressions, not merely load the example files."""

import copy
import sys
import unittest
from pathlib import Path

from jsonschema.exceptions import ValidationError

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import validate_contracts as contract


class ContractTests(unittest.TestCase):
    def setUp(self):
        self.corpus = contract.load(contract.CONTRACT / "fixtures/scenarios.json")

    def case(self, name):
        return next(case for case in self.corpus["scenarios"] if case["id"] == name)

    def rejected(self):
        with self.assertRaises((ValueError, ValidationError)):
            contract.check_corpus(self.corpus)

    def test_all_reference_examples(self):
        self.assertEqual(contract.validate_all(), 27)

    def test_duplicate_write_on_retry_is_rejected(self):
        self.case("identical-retry")["after"]["recordCount"] = 2
        self.rejected()

    def test_stale_retry_must_return_original_receipt(self):
        self.case("identical-retry")["response"]["status"] = 409
        self.rejected()

    def test_conflicting_retry_cannot_overwrite(self):
        self.case("conflicting-retry")["response"] = copy.deepcopy(self.case("write-create")["response"])
        self.rejected()

    def test_spoofed_identity_cannot_authenticate(self):
        self.case("spoofed-proxy")["response"] = copy.deepcopy(self.case("trusted-owner-proxy")["response"])
        self.rejected()

    def test_records_grant_cannot_enable_native_code(self):
        self.case("agent-cannot-install")["response"]["status"] = 200
        self.rejected()

    def test_revoked_idempotent_retry_cannot_return_success(self):
        self.case("revoked-retry")["response"] = copy.deepcopy(self.case("identical-retry")["response"])
        self.rejected()

    def test_expiry_boundary_is_exclusive(self):
        self.case("pairing-expired")["response"]["status"] = 201
        self.rejected()

    def test_consumed_pairing_cannot_reissue_token(self):
        self.case("pairing-replayed")["response"] = copy.deepcopy(self.case("pairing-redeemed")["response"])
        self.rejected()

    def test_device_pairing_cannot_grant_record_reads(self):
        self.case("pairing-redeemed")["response"]["body"]["data"]["scopes"].append("records:read")
        self.rejected()

    def test_partial_receipt_cannot_advance_healthkit_anchor(self):
        self.case("healthkit-accepted")["response"]["body"]["deletionsAccepted"] = 0
        self.rejected()

    def test_missing_epoch_cannot_fall_back_to_legacy_ingest(self):
        self.case("healthkit-missing-epoch")["response"] = copy.deepcopy(self.case("healthkit-accepted")["response"])
        self.rejected()

    def test_replacement_cannot_duplicate_observation(self):
        self.case("replacement-same-observation")["after"]["canonicalCount"] = 2
        self.rejected()

    def test_healthkit_empty_read_is_not_proof_of_denial(self):
        sources = self.case("capabilities")["response"]["body"]["data"]["sources"]
        sources[2]["missingness"] = "permission_denied"
        self.rejected()

    def test_receipt_revision_is_bound_to_commit(self):
        self.case("write-create")["response"]["body"]["meta"]["dataRevision"] = 99
        self.rejected()

    def test_missing_scenario_fails_coverage(self):
        self.corpus["scenarios"].pop()
        self.rejected()

    def test_compatibility_rejects_incompatible_agent(self):
        manifest = contract.load(contract.CONTRACT / "compatibility.json")
        manifest["components"]["codex"]["requires"]["api"] = [2]
        with self.assertRaisesRegex(ValueError, "incompatible"):
            contract.check_manifest(manifest)

    def test_no_fabricated_release_artifact(self):
        manifest = contract.load(contract.CONTRACT / "compatibility.json")
        manifest["release"]["artifacts"] = ["unpublished-image"]
        with self.assertRaises(ValidationError):
            contract.check_manifest(manifest)

    def test_restore_rotates_epoch_and_credentials_and_preserves_files(self):
        original = contract.load(contract.CONTRACT / "fixtures/lifecycle.json")
        for failure in ["epoch", "credential", "source", "tests", "state"]:
            with self.subTest(failure=failure):
                changed = copy.deepcopy(original)
                restore = changed["restore"]
                if failure == "epoch":
                    restore["after"]["identity"]["restoreEpoch"] = restore["before"]["identity"]["restoreEpoch"]
                elif failure == "credential":
                    restore["after"]["activeGrants"] = restore["before"]["activeGrants"]
                else:
                    part = "src" if failure == "source" else failure
                    restore["restoredInventory"] = [p for p in restore["restoredInventory"] if f"/{part}/" not in p]
                with self.assertRaises(ValueError):
                    contract.check_lifecycle(changed)

    def test_incompatible_extension_cannot_activate(self):
        lifecycle = contract.load(contract.CONTRACT / "fixtures/lifecycle.json")
        lifecycle["upgrades"][1]["outcome"] = "activate"
        with self.assertRaisesRegex(ValueError, "unsafe upgrade"):
            contract.check_lifecycle(lifecycle)

    def test_metric_missing_data_is_not_zero(self):
        example = contract.load(contract.CONTRACT / "examples/weekly-mass.json")
        example["cases"][1]["output"]["value"] = 0
        with self.assertRaisesRegex(ValueError, "metric wrong"):
            contract.check_extension(example)

    def test_connector_wrong_unit_conversion_rejected(self):
        example = contract.load(contract.CONTRACT / "examples/water-import.json")
        for case in example["cases"]:
            case["output"]["value"] = 250
        with self.assertRaisesRegex(ValueError, "unit normalization"):
            contract.check_extension(example)

    def test_extension_cannot_escape_personal_root(self):
        example = contract.load(contract.CONTRACT / "examples/weekly-mass.json")
        example["manifest"]["entrypoints"]["metric"] = "../../core.py:run"
        with self.assertRaises(ValidationError):
            contract.check_extension(example)

    def test_duplicate_json_keys_rejected(self):
        with self.assertRaisesRegex(ValueError, "duplicate JSON"):
            contract.unique_object([("value", 1), ("value", 2)])


if __name__ == "__main__":
    unittest.main()
