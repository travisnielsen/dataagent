"""Unit tests for Azure AD tenant allowlist configuration."""

from api.middleware.auth import AzureADSettings, build_valid_issuers, parse_tenant_ids


class TestParseTenantIds:
    def test_parse_tenant_ids_strips_and_deduplicates(self) -> None:
        tenant_ids = parse_tenant_ids(" tenant-a,tenant-b, tenant-a ,,tenant-c ")

        assert tenant_ids == ["tenant-a", "tenant-b", "tenant-c"]


class TestBuildValidIssuers:
    def test_build_valid_issuers_includes_v1_and_v2_formats(self) -> None:
        issuers = build_valid_issuers(["tenant-a", "tenant-b"])

        assert issuers == [
            "https://login.microsoftonline.com/tenant-a/v2.0",
            "https://sts.windows.net/tenant-a/",
            "https://login.microsoftonline.com/tenant-b/v2.0",
            "https://sts.windows.net/tenant-b/",
        ]


class TestAzureADSettings:
    def test_allowed_tenant_ids_prefers_allowlist_variable(self) -> None:
        settings = AzureADSettings.model_construct(
            AZURE_AD_CLIENT_ID="client-id",
            AZURE_AD_TENANT_ID="legacy-tenant",
            AZURE_AD_TENANT_IDS="tenant-a, tenant-b",
        )

        assert settings.allowed_tenant_ids == ["tenant-a", "tenant-b"]
        assert settings.auth_enabled is True

    def test_allowed_tenant_ids_falls_back_to_legacy_variable(self) -> None:
        settings = AzureADSettings.model_construct(
            AZURE_AD_CLIENT_ID="client-id",
            AZURE_AD_TENANT_ID="legacy-tenant",
        )

        assert settings.allowed_tenant_ids == ["legacy-tenant"]
        assert settings.auth_enabled is True
