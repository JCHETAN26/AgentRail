"""The authorisation matrix, asserted exhaustively.

These are the highest-value unit tests in the repository: every route delegates
its access decision here, so a mistake in this table is a mistake everywhere.
"""

from __future__ import annotations

import itertools

import pytest

from agentrail_core.identity.roles import (
    ROLE_PERMISSIONS,
    AuthorisationError,
    Permission,
    Principal,
    PrincipalKind,
    Role,
    authorize,
)

ORG = "01ARZ3NDEKTSV4RRFFQ69G5FAV"
OTHER_ORG = "01BX5ZZKBKACTAV9WEVGEMMVRY"


def user(role: Role, *, organisation_id: str = ORG) -> Principal:
    return Principal(kind=PrincipalKind.USER, id="u1", organisation_id=organisation_id, role=role)


def service_account(
    role: Role, scopes: frozenset[Permission] | None, *, organisation_id: str = ORG
) -> Principal:
    return Principal(
        kind=PrincipalKind.API_KEY,
        id="k1",
        organisation_id=organisation_id,
        role=role,
        scopes=scopes,
    )


class TestRoleMatrix:
    def test_every_role_has_an_entry(self) -> None:
        assert set(ROLE_PERMISSIONS) == set(Role)

    @pytest.mark.parametrize("role", list(Role))
    def test_every_role_can_read_its_organisation(self, role: Role) -> None:
        assert user(role).can(Permission.ORGANISATION_READ)

    @pytest.mark.parametrize("role", [Role.VIEWER, Role.REVIEWER])
    def test_read_only_roles_cannot_create_jobs(self, role: Role) -> None:
        assert not user(role).can(Permission.JOB_CREATE)

    @pytest.mark.parametrize("role", [Role.DEVELOPER, Role.ADMIN, Role.OWNER])
    def test_writing_roles_can_create_jobs(self, role: Role) -> None:
        assert user(role).can(Permission.JOB_CREATE)

    @pytest.mark.parametrize("role", [Role.VIEWER, Role.REVIEWER, Role.DEVELOPER])
    def test_only_admins_and_owners_manage_members_and_keys(self, role: Role) -> None:
        assert not user(role).can(Permission.MEMBER_MANAGE)
        assert not user(role).can(Permission.API_KEY_MANAGE)

    @pytest.mark.parametrize("role", [Role.ADMIN, Role.OWNER])
    def test_admins_manage_members_and_keys(self, role: Role) -> None:
        assert user(role).can(Permission.MEMBER_MANAGE)
        assert user(role).can(Permission.API_KEY_MANAGE)

    def test_viewer_cannot_read_the_audit_log(self) -> None:
        assert not user(Role.VIEWER).can(Permission.AUDIT_READ)

    def test_a_viewer_can_see_an_approval_but_not_decide_it(self) -> None:
        """The distinction the reviewer role was created for in Phase 1 and only
        became load-bearing here: watching a run is not authorising it."""
        assert user(Role.VIEWER).can(Permission.APPROVAL_READ)
        assert not user(Role.VIEWER).can(Permission.APPROVAL_DECIDE)

    @pytest.mark.parametrize("role", [Role.REVIEWER, Role.DEVELOPER, Role.ADMIN, Role.OWNER])
    def test_reviewers_and_above_decide_approvals(self, role: Role) -> None:
        assert user(role).can(Permission.APPROVAL_DECIDE)

    def test_roles_are_cumulative(self) -> None:
        """Each role is a superset of the one below, so a promotion never removes access."""
        ladder = [Role.VIEWER, Role.REVIEWER, Role.DEVELOPER, Role.ADMIN, Role.OWNER]
        for lower, higher in itertools.pairwise(ladder):
            assert ROLE_PERMISSIONS[lower] <= ROLE_PERMISSIONS[higher], f"{lower} ⊄ {higher}"


class TestTenancy:
    @pytest.mark.parametrize("role", list(Role))
    def test_no_role_can_act_in_another_organisation(self, role: Role) -> None:
        """The central tenancy guarantee. Every role, every permission."""
        principal = user(role)
        for permission in Permission:
            with pytest.raises(AuthorisationError):
                authorize(principal, permission, organisation_id=OTHER_ORG)

    def test_owner_of_one_org_is_nobody_in_another(self) -> None:
        with pytest.raises(AuthorisationError):
            authorize(user(Role.OWNER), Permission.ORGANISATION_READ, organisation_id=OTHER_ORG)

    def test_authorize_passes_within_the_principals_own_organisation(self) -> None:
        authorize(user(Role.OWNER), Permission.ORGANISATION_READ, organisation_id=ORG)

    def test_missing_permission_and_wrong_tenant_raise_the_same_type(self) -> None:
        """A caller must not be able to tell 'not yours' from 'not permitted'."""
        with pytest.raises(AuthorisationError) as wrong_tenant:
            authorize(user(Role.OWNER), Permission.JOB_READ, organisation_id=OTHER_ORG)

        with pytest.raises(AuthorisationError) as missing_permission:
            authorize(user(Role.VIEWER), Permission.MEMBER_MANAGE, organisation_id=ORG)

        assert type(wrong_tenant.value) is type(missing_permission.value)


class TestApiKeyScopes:
    def test_no_scopes_means_the_full_role(self) -> None:
        assert service_account(Role.DEVELOPER, None).permissions == ROLE_PERMISSIONS[Role.DEVELOPER]

    def test_scopes_narrow_the_role(self) -> None:
        key = service_account(Role.DEVELOPER, frozenset({Permission.JOB_READ}))

        assert key.can(Permission.JOB_READ)
        assert not key.can(Permission.JOB_CREATE)

    def test_scopes_cannot_widen_beyond_the_role(self) -> None:
        """A stolen key is bounded twice: by its role and by its scopes."""
        key = service_account(Role.VIEWER, frozenset({Permission.MEMBER_MANAGE}))

        assert not key.can(Permission.MEMBER_MANAGE)
        assert key.permissions == ROLE_PERMISSIONS[Role.VIEWER] & frozenset(
            {Permission.MEMBER_MANAGE}
        )

    def test_an_empty_scope_set_grants_nothing(self) -> None:
        assert service_account(Role.OWNER, frozenset()).permissions == frozenset()

    def test_service_accounts_are_identifiable(self) -> None:
        assert service_account(Role.DEVELOPER, None).is_service_account
        assert not user(Role.DEVELOPER).is_service_account

    def test_a_scoped_key_still_cannot_cross_tenants(self) -> None:
        key = service_account(Role.OWNER, None, organisation_id=ORG)

        with pytest.raises(AuthorisationError):
            authorize(key, Permission.JOB_READ, organisation_id=OTHER_ORG)
