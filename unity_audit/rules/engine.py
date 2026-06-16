"""Rule Engine - Execute deterministic rules and produce Issues."""

from collections.abc import Callable
from dataclasses import dataclass, field

from unity_audit.meta_parser import MetaInfo
from unity_audit.scanner import AssetInfo


@dataclass
class Issue:
    """A single issue found by a rule."""
    issue_id: str
    rule_id: str
    severity: str          # critical, high, medium, low
    asset_path: str
    title: str
    message: str
    evidence: dict = field(default_factory=dict)
    suggestion: str = ""
    auto_fixable: bool = False


# Rule function signature: (AssetInfo, MetaInfo, extracted_info) -> Optional[Issue]
RuleFunc = Callable[[AssetInfo, MetaInfo, object], Issue | None]


class RuleEngine:
    """Deterministic rule engine that evaluates rules against assets.

    Rules must be deterministic and reproducible. LLM is NOT involved in
    basic rule evaluation.
    """

    def __init__(self):
        self._rules: list[tuple[str, RuleFunc, str]] = []  # (rule_id, func, asset_type)

    def register(self, rule_id: str, asset_type: str, func: RuleFunc):
        """Register a rule function.

        Args:
            rule_id: Unique rule identifier (e.g., TEX_UI_MIPMAP_ENABLED).
            asset_type: Which asset type this rule applies to (Texture, Audio, Prefab, Scene).
            func: Rule function that takes (AssetInfo, MetaInfo, extracted_info) and
                  returns Optional[Issue].
        """
        self._rules.append((rule_id, func, asset_type))

    def evaluate(
        self,
        asset: AssetInfo,
        meta: MetaInfo,
        extracted_info: object,
    ) -> list[Issue]:
        """Evaluate all applicable rules against a single asset.

        Args:
            asset: The AssetInfo from the scanner.
            meta: The MetaInfo from the meta parser.
            extracted_info: The extracted asset-specific info (TextureInfo, AudioInfo, etc.)

        Returns:
            List of Issues found.
        """
        issues = []
        for rule_id, func, asset_type in self._rules:
            if asset_type != asset.asset_type:
                continue
            try:
                issue = func(asset, meta, extracted_info)
                if issue is not None:
                    issues.append(issue)
            except Exception as e:
                # Rule failure should not crash the scan - record but continue
                issues.append(Issue(
                    issue_id=f"RULE_ERR_{rule_id}_{asset.asset_path}",
                    rule_id=rule_id,
                    severity="low",
                    asset_path=asset.asset_path,
                    title=f"Rule evaluation error: {rule_id}",
                    message=f"Rule {rule_id} failed with error: {e}",
                    evidence={"error": str(e)},
                ))
        return issues

    def evaluate_all(
        self,
        assets: list[AssetInfo],
        meta_map: dict[str, MetaInfo],
        extracted_map: dict[str, object],
    ) -> list[Issue]:
        """Evaluate all rules against all assets.

        Args:
            assets: List of all scanned assets.
            meta_map: Mapping of asset_path -> MetaInfo.
            extracted_map: Mapping of asset_path -> extracted info object.

        Returns:
            Combined list of all Issues.
        """
        all_issues = []
        for asset in assets:
            meta = meta_map.get(asset.asset_path)
            extracted = extracted_map.get(asset.asset_path)
            if meta is None:
                meta = MetaInfo(parse_error="Meta not loaded")
            if extracted is None:
                continue  # Skip assets we could not extract
            issues = self.evaluate(asset, meta, extracted)
            # Assign unique issue IDs
            for i, issue in enumerate(issues):
                issue.issue_id = f"{issue.rule_id}_{i}_{asset.asset_path.replace('/', '_').replace('.', '_')}"
            all_issues.extend(issues)
        return all_issues
