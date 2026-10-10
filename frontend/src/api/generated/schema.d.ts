export interface paths {
    "/api/v1/browser/csrf/": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * Seed the CSRF cookie
         * @description Sets the readable CSRF cookie and returns no body. The browser copies the cookie value into the `X-CSRFToken` header of every unsafe request.
         */
        get: operations["browser_csrf"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        /**
         * Check the CSRF route without a body
         * @description Runs the same negotiation and throttling boundaries as GET and sets the same CSRF cookie while returning status and headers only.
         */
        head: operations["browser_csrf_head"];
        patch?: never;
        trace?: never;
    };
    "/api/v1/browser/login/": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /**
         * Sign in with HttpOnly cookies
         * @description Authenticates an active account without revealing whether a rejected username exists, then sets the HttpOnly access and refresh cookies and rotates the CSRF cookie. The body is the caller's profile; no token appears in any body or URL. Requires a matching `X-CSRFToken` header and a trusted `Origin`.
         */
        post: operations["browser_login"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/browser/logout/": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /**
         * End the browser session
         * @description Blacklists the refresh token, clears all three cookies, and closes the account's open cookie-authenticated sockets. Safe to repeat: an absent, expired, or already revoked session still returns 204. Requires a matching `X-CSRFToken` header and a trusted `Origin` whenever a credential cookie is present. A previously issued access token stays valid until it expires.
         */
        post: operations["browser_logout"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/browser/refresh/": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /**
         * Rotate the HttpOnly credential cookies
         * @description Exchanges the refresh cookie for a new access and refresh cookie pair. The presented refresh token is blacklisted atomically, so replaying it fails. Any unauthorized outcome clears all three cookies. Requires a matching `X-CSRFToken` header and a trusted `Origin` whenever the refresh cookie is present.
         */
        post: operations["browser_refresh"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/jwt/create/": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /**
         * Exchange credentials for JSON web tokens
         * @description Authenticates an active account without revealing whether a rejected username exists. The response contains only a short-lived access token and a longer-lived refresh token.
         */
        post: operations["jwt_create"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/jwt/refresh/": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /**
         * Rotate a JSON web refresh token
         * @description Exchanges one valid refresh token for new access and refresh credentials. The account is locked before outstanding-token state, and the supplied refresh token is blacklisted atomically before its replacement is persisted.
         */
        post: operations["jwt_refresh"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/jwt/verify/": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /**
         * Verify a JSON web token
         * @description Reports whether an access or refresh token is currently valid without returning its identity or protocol claims.
         */
        post: operations["jwt_verify"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/token/login/": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /**
         * Exchange credentials for a token
         * @description Authenticates an active account without revealing whether a rejected username exists. Stored password profiles outside the accepted bounded policy receive the same generic failure and fixed dummy work as an unknown account and require password reset. The returned DRF token is a single non-expiring credential stored in plaintext as its table key and should be used only where the secondary scheme is appropriate.
         */
        post: operations["token_login"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/token/logout/": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /**
         * Destroy the caller's token
         * @description Deletes the DRF token that authenticated this request. The credential is stored in plaintext as the authentication table key and otherwise remains non-expiring until this operation revokes it.
         */
        post: operations["token_logout"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/users/": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /**
         * Register or activate an account
         * @description Accepts exactly one of two request branches. A registration body contains username, email, password, and password_confirm and returns 201 with the submitted public identifiers. An activation body contains account and token and returns bodyless 204 after consuming a valid token. A username or email already occupied under PostgreSQL LOWER identity semantics returns the same status and body as creation, always 201, so account existence is disclosed only by the later activation email. Every completed 201 response observes the environment-configured monotonic minimum duration.
         */
        post: operations["user_registration_or_activation"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/users/me/": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * Return the caller's profile
         * @description Returns only the account resolved from the supplied credential. Query parameters are ignored and no path or request identifier can select another account.
         */
        get: operations["user_profile_retrieve"];
        put?: never;
        post?: never;
        /**
         * Delete the caller's account
         * @description Requires the current password and irreversibly deletes the authenticated account. No identifier can select another account.
         */
        delete: operations["user_profile_delete"];
        options?: never;
        /**
         * Check availability of the caller's profile
         * @description Runs the same authentication, authorization, dependency, negotiation, and authenticated-read admission boundaries as GET without returning a response body.
         */
        head: operations["user_profile_head"];
        /**
         * Update the caller's mutable profile
         * @description Partially updates only email. The account id, username, password, activation flag, staff and superuser flags, groups, and permissions are rejected as undeclared fields.
         */
        patch: operations["user_profile_update"];
        trace?: never;
    };
    "/api/v1/users/resend_activation/": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /**
         * Resend account activation
         * @description Accepts an email address and returns the same response for unknown, inactive, and active accounts. Only an inactive matching account receives a fresh activation link.
         */
        post: operations["user_activation_resend"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/users/reset_password/": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /**
         * Request a password-reset email
         * @description Returns the same status, body, and configured minimum public duration whether an active account matches the normalized address or not. Only a matching active account receives an account-bound, expiring, single-use link. Reset-token insert failure rolls back partial state and takes the same accepted dummy-publication path.
         */
        post: operations["user_password_reset_request"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/users/reset_password_confirm/": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /**
         * Confirm a password reset
         * @description Accepts the account key and token from the email with a replacement password. Success consumes every outstanding reset link, revokes all existing credentials and sessions, and sends a credential-free password-change notification. Admission validation is account-independent; complete account-sensitive validation runs only after the bearer and locked account authenticate.
         */
        post: operations["user_password_reset_confirm"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/users/reset_username/": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /**
         * Request a username-reset email
         * @description Returns the same status, body, and configured minimum public duration whether an active account matches the normalized address or not. Only a matching active account receives an account-bound, expiring, single-use link. Token insert failure rolls back partial state and takes the same accepted dummy-publication path.
         */
        post: operations["user_username_reset_request"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/users/reset_username_confirm/": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /**
         * Confirm a username reset
         * @description Accepts the account key and token from the email with a replacement username. Success consumes every outstanding username-reset link and sends a notification without revoking DRF tokens, JSON web tokens, or Django sessions issued before the change.
         */
        post: operations["user_username_reset_confirm"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/users/set_password/": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /**
         * Change the caller's password
         * @description Requires the current password, a distinct replacement, and confirmation. Every configured password validator applies. Success invalidates all DRF tokens, JSON web tokens, and Django sessions, including the caller credential used for this request; the client must authenticate again.
         */
        post: operations["user_password_change"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/users/set_username/": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /**
         * Change the caller's username
         * @description Requires the current password and a format-valid, case-insensitively available username. Unlike password changes, success does not invalidate any DRF token, JSON web token, or Django session issued before the change.
         */
        post: operations["user_username_change"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/health/": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * Check application readiness
         * @description Reports process liveness and whether every required backing service can support traffic. Dependency diagnostics are available only to authenticated staff.
         */
        get: operations["health_readiness"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        /**
         * Check application readiness without a body
         * @description Runs the same public readiness checks and negotiation boundaries as GET while returning status and headers only.
         */
        head: operations["health_readiness_head"];
        patch?: never;
        trace?: never;
    };
}
export type webhooks = Record<string, never>;
export interface components {
    schemas: {
        /**
         * @description Validate one activation confirmation request.
         *
         *     Inherits from ``StrictRequestSerializer`` and accepts only the public account key carried in
         *     the link and its signed one-time token.
         *
         *     Attributes:
         *         account: Account key copied from the activation link.
         *         token: Signed one-time activation value.
         *
         *     Members:
         *         None beyond those inherited from ``Serializer``.
         */
        ActivationConfirmation: {
            /** Format: uuid */
            account: string;
            token: string;
        };
        /**
         * @description Validate one activation resend request.
         *
         *     Inherits from ``StrictRequestSerializer`` and accepts only an email address, canonicalizing
         *     it to the same form account persistence uses before any lookup or throttle identity is derived.
         *
         *     Attributes:
         *         email: Address that may belong to an inactive account.
         *
         *     Members:
         *         validate_email: Canonicalize and revalidate the submitted address.
         */
        ActivationResend: {
            /** Format: email */
            email: string;
        };
        /**
         * @description Describe the enumeration-resistant resend response.
         *
         *     Inherits from DRF's ``Serializer`` and exposes one accepted statement shared by unknown,
         *     inactive, and active account outcomes.
         *
         *     Attributes:
         *         detail: State-independent accepted response text.
         *
         *     Members:
         *         None beyond those inherited from ``Serializer``.
         */
        ActivationResendResponse: {
            readonly detail: string;
        };
        /**
         * @description Describe the shared correlated API error envelope.
         *
         *     Inherits from DRF's ``Serializer`` and mirrors the stable fields produced by the central
         *     exception handler while allowing validation details to retain their field-keyed structure.
         *
         *     Attributes:
         *         code: Stable machine-readable error code.
         *         message: Safe human-readable message.
         *         details: Per-field validation details or an empty mapping.
         *         request_id: Correlation identifier shared with the response header.
         *
         *     Members:
         *         None beyond those inherited from ``Serializer``.
         */
        ErrorEnvelope: {
            readonly code: string;
            readonly message: string;
            readonly details: {
                [key: string]: string[];
            };
            readonly request_id: string;
        };
        /**
         * @description Describe a successful JSON web token credential exchange.
         *
         *     Inherits from DRF's ``Serializer`` and exposes only the two credentials the protocol requires,
         *     keeping account attributes and other personal data outside the response.
         *
         *     Attributes:
         *         access: Short-lived bearer credential.
         *         refresh: Longer-lived rotation credential.
         *
         *     Members:
         *         None beyond those inherited from ``Serializer``.
         */
        JWTCreateResponse: {
            readonly access: string;
            readonly refresh: string;
        };
        /**
         * @description Validate a refresh-token exchange request.
         *
         *     Inherits from ``StrictRequestSerializer`` and accepts only the credential being rotated,
         *     keeping the original value write-only so it cannot enter a rendered success response.
         *
         *     Attributes:
         *         refresh: Refresh credential supplied by the client.
         *
         *     Members:
         *         None beyond those inherited from ``Serializer``.
         */
        JWTRefreshRequest: {
            refresh: string;
        };
        /**
         * @description Describe a successful refresh-token rotation.
         *
         *     Inherits from DRF's ``Serializer`` and exposes the new access credential together with the
         *     rotated refresh credential that replaces the now-blacklisted input.
         *
         *     Attributes:
         *         access: Newly issued short-lived bearer credential.
         *         refresh: Newly issued refresh credential.
         *
         *     Members:
         *         None beyond those inherited from ``Serializer``.
         */
        JWTRefreshResponse: {
            readonly access: string;
            readonly refresh: string;
        };
        /**
         * @description Validate a token verification request.
         *
         *     Inherits from ``StrictRequestSerializer`` and accepts one credential without ever serializing
         *     it back to the caller.
         *
         *     Attributes:
         *         token: Access or refresh credential whose validity is being checked.
         *
         *     Members:
         *         None beyond those inherited from ``Serializer``.
         */
        JWTVerifyRequest: {
            token: string;
        };
        /**
         * @description Validate one authenticated password replacement body.
         *
         *     Inherits from ``StrictRequestSerializer`` and accepts only the current credential, its
         *     replacement, and confirmation while keeping every password write-only.
         *
         *     Attributes:
         *         current_password: Existing raw credential.
         *         new_password: Replacement raw credential.
         *         new_password_confirm: Repeated replacement credential.
         *
         *     Members:
         *         validate: Require matching replacement fields.
         */
        PasswordChange: {
            current_password: string;
            new_password: string;
            new_password_confirm: string;
        };
        /**
         * @description Validate one password-reset confirmation body.
         *
         *     Inherits from ``StrictRequestSerializer`` and accepts only the immutable account key, reset
         *     bearer, replacement password, and matching confirmation.
         *
         *     Attributes:
         *         account: Immutable account key carried by the email link.
         *         token: Account-bound password-reset bearer.
         *         new_password: Replacement raw credential.
         *         new_password_confirm: Repeated replacement credential.
         *
         *     Members:
         *         validate: Require matching replacement fields.
         */
        PasswordResetConfirm: {
            /** Format: uuid */
            account: string;
            token: string;
            new_password: string;
            new_password_confirm: string;
        };
        /**
         * @description Validate one public password-reset request.
         *
         *     Inherits from ``StrictRequestSerializer`` and accepts only an email address normalized to the
         *     same form account persistence and authoritative identity lookup use.
         *
         *     Attributes:
         *         email: Address that may belong to an active account.
         *
         *     Members:
         *         validate_email: Normalize and revalidate the submitted address.
         */
        PasswordResetRequest: {
            /** Format: email */
            email: string;
        };
        /**
         * @description Describe the enumeration-resistant reset response.
         *
         *     Inherits from ``StrictRequestSerializer`` and exposes only the state-independent accepted
         *     statement shared by known and unknown addresses.
         *
         *     Attributes:
         *         detail: State-independent accepted response text.
         *
         *     Members:
         *         None beyond those inherited from ``Serializer``.
         */
        PasswordResetResponse: {
            readonly detail: string;
        };
        /**
         * @description Validate the sole mutable profile field.
         *
         *     Inherits from ``StrictRequestSerializer`` and accepts only email, rejecting identifiers,
         *     credentials, activation state, and permissions instead of silently ignoring them.
         *
         *     Attributes:
         *         email: Replacement public address.
         *
         *     Members:
         *         validate_email: Canonicalize the replacement address.
         */
        PatchedUserProfileUpdate: {
            /** Format: email */
            email?: string;
        };
        /**
         * @description Validate one public account registration.
         *
         *     Inherits from ``StrictRequestSerializer`` and checks the submitted password against every
         *     configured validator using the candidate identifiers, while keeping both password fields
         *     write-only.
         *
         *     Attributes:
         *         username: Public account name.
         *         email: Public account address.
         *         password: Raw credential accepted only as input.
         *         password_confirm: Repeated raw credential accepted only as input.
         *
         *     Members:
         *         validate_email: Canonicalize the address before validation completes.
         *         validate: Enforce confirmation and configured password policy.
         */
        Registration: {
            username: string;
            /** Format: email */
            email: string;
            password: string;
            password_confirm: string;
        };
        /**
         * @description Describe the enumeration-resistant registration response.
         *
         *     Inherits from DRF's ``Serializer`` and exposes only the accepted username and normalized email,
         *     omitting database identity and account state so a duplicate request can return the same body.
         *
         *     Attributes:
         *         username: Submitted public account name.
         *         email: Submitted normalized email address.
         *
         *     Members:
         *         None beyond those inherited from ``Serializer``.
         */
        RegistrationResponse: {
            readonly username: string;
            /** Format: email */
            readonly email: string;
        };
        /**
         * @description Validate the two token-login credential fields.
         *
         *     Inherits from ``StrictRequestSerializer`` and accepts only username and password, leaving
         *     account lookup and password verification to the view's credential verifier.
         *
         *     Attributes:
         *         username: Account username supplied by the client.
         *         password: Raw password supplied by the client and never returned.
         *
         *     Members:
         *         None beyond those inherited from ``Serializer``.
         */
        TokenLogin: {
            username: string;
            password: string;
        };
        /**
         * @description Describe the successful token-login body.
         *
         *     Inherits from DRF's ``Serializer`` and exposes only the credential clients present in the
         *     authorization header, preventing account data from becoming part of the login contract.
         *
         *     Attributes:
         *         token: DRF token value issued to the account.
         *
         *     Members:
         *         None beyond those inherited from ``Serializer``.
         */
        TokenResponse: {
            readonly token: string;
        };
        /**
         * @description Describe the authenticated caller's public profile.
         *
         *     Inherits from DRF's ``Serializer`` and exposes the immutable key, two public identifiers, and
         *     the closed capability list, omitting activation, permission, password, token, and operational
         *     timestamp state.
         *
         *     Attributes:
         *         id: Immutable account identifier.
         *         username: Public account name changed only through its dedicated route.
         *         email: Public address and the sole mutable profile field.
         *         capabilities: Read-only registry entries a client may use to shape navigation.
         *
         *     Members:
         *         get_capabilities: Derive the capability list from the account.
         */
        UserProfile: {
            /** Format: uuid */
            readonly id: string;
            readonly username: string;
            /** Format: email */
            readonly email: string;
            /** @description Closed registry guarding client navigation only; every route still enforces authorization itself. */
            readonly capabilities: ("account:read" | "account:update" | "account:delete" | "credentials:change" | "notifications:receive")[];
        };
        UserRegistrationOrActivation: components["schemas"]["Registration"] | components["schemas"]["ActivationConfirmation"];
        /**
         * @description Validate one authenticated username replacement body.
         *
         *     Inherits from ``StrictRequestSerializer`` and accepts only the current password and replacement
         *     username while keeping the password write-only.
         *
         *     Attributes:
         *         current_password: Existing raw credential.
         *         new_username: Replacement login identifier.
         *
         *     Members:
         *         None beyond field validation.
         */
        UsernameChange: {
            current_password: string;
            new_username: string;
        };
        /**
         * @description Validate one username-reset confirmation body.
         *
         *     Inherits from ``StrictRequestSerializer`` and accepts only the immutable account key, reset
         *     bearer, and replacement username.
         *
         *     Attributes:
         *         account: Immutable account key carried by the email link.
         *         token: Account-bound username-reset bearer.
         *         new_username: Replacement login identifier.
         *
         *     Members:
         *         None beyond field validation.
         */
        UsernameResetConfirm: {
            /** Format: uuid */
            account: string;
            token: string;
            new_username: string;
        };
        /**
         * @description Validate one public username-reset request.
         *
         *     Inherits from ``StrictRequestSerializer`` and accepts only an email address normalized to the
         *     same form account persistence and authoritative identity lookup use.
         *
         *     Attributes:
         *         email: Address that may belong to an active account.
         *
         *     Members:
         *         validate_email: Normalize and revalidate the submitted address.
         */
        UsernameResetRequest: {
            /** Format: email */
            email: string;
        };
        /**
         * @description Describe the enumeration-resistant username-reset response.
         *
         *     Inherits from ``StrictRequestSerializer`` and exposes only the state-independent accepted
         *     statement shared by known and unknown addresses.
         *
         *     Attributes:
         *         detail: State-independent accepted response text.
         *
         *     Members:
         *         None beyond those inherited from ``Serializer``.
         */
        UsernameResetResponse: {
            readonly detail: string;
        };
        UserProfileDeletion: {
            current_password: string;
        };
        HealthReadyChecks: {
            /** @constant */
            broker: "working";
            /** @constant */
            cache: "working";
            /** @constant */
            channel_layer: "working";
            /** @constant */
            database_primary: "working";
            /** @constant */
            database_replica: "working";
            /** @constant */
            mail: "working";
            /** @constant */
            object_storage: "working";
        };
        HealthNotReadyChecks: {
            /** @enum {string} */
            broker: "working" | "unavailable";
            /** @enum {string} */
            cache: "working" | "unavailable";
            /** @enum {string} */
            channel_layer: "working" | "unavailable";
            /** @enum {string} */
            database_primary: "working" | "unavailable";
            /** @enum {string} */
            database_replica: "working" | "unavailable";
            /** @enum {string} */
            mail: "working" | "unavailable";
            /** @enum {string} */
            object_storage: "working" | "unavailable";
        } | {
            /** @constant */
            broker: "unavailable";
        } | {
            /** @constant */
            cache: "unavailable";
        } | {
            /** @constant */
            channel_layer: "unavailable";
        } | {
            /** @constant */
            database_primary: "unavailable";
        } | {
            /** @constant */
            database_replica: "unavailable";
        } | {
            /** @constant */
            mail: "unavailable";
        } | {
            /** @constant */
            object_storage: "unavailable";
        };
        HealthWorkingDiagnostic: {
            /** @constant */
            status: "working";
            duration_ms: number;
            error: null;
        };
        HealthUnavailableDiagnostic: {
            /** @constant */
            status: "unavailable";
            duration_ms: number;
            /** @constant */
            error: "dependency_unavailable";
        };
        HealthDiagnostic: components["schemas"]["HealthWorkingDiagnostic"] | components["schemas"]["HealthUnavailableDiagnostic"];
        HealthStaffStatusCoupling: unknown & unknown & unknown & unknown & unknown & unknown & unknown & unknown & unknown & unknown & unknown & unknown & unknown & unknown;
        HealthReadyDetails: {
            broker: components["schemas"]["HealthWorkingDiagnostic"];
            cache: components["schemas"]["HealthWorkingDiagnostic"];
            channel_layer: components["schemas"]["HealthWorkingDiagnostic"];
            database_primary: components["schemas"]["HealthWorkingDiagnostic"];
            database_replica: components["schemas"]["HealthWorkingDiagnostic"];
            mail: components["schemas"]["HealthWorkingDiagnostic"];
            object_storage: components["schemas"]["HealthWorkingDiagnostic"];
        };
        HealthNotReadyDetails: {
            broker: components["schemas"]["HealthDiagnostic"];
            cache: components["schemas"]["HealthDiagnostic"];
            channel_layer: components["schemas"]["HealthDiagnostic"];
            database_primary: components["schemas"]["HealthDiagnostic"];
            database_replica: components["schemas"]["HealthDiagnostic"];
            mail: components["schemas"]["HealthDiagnostic"];
            object_storage: components["schemas"]["HealthDiagnostic"];
        } | {
            broker: components["schemas"]["HealthUnavailableDiagnostic"];
        } | {
            cache: components["schemas"]["HealthUnavailableDiagnostic"];
        } | {
            channel_layer: components["schemas"]["HealthUnavailableDiagnostic"];
        } | {
            database_primary: components["schemas"]["HealthUnavailableDiagnostic"];
        } | {
            database_replica: components["schemas"]["HealthUnavailableDiagnostic"];
        } | {
            mail: components["schemas"]["HealthUnavailableDiagnostic"];
        } | {
            object_storage: components["schemas"]["HealthUnavailableDiagnostic"];
        };
        HealthReadyPublic: {
            /** @constant */
            status: "ready";
            /** @constant */
            liveness: "alive";
            /** @constant */
            readiness: "ready";
            checks: components["schemas"]["HealthReadyChecks"];
        };
        HealthReadyStaff: {
            /** @constant */
            status: "ready";
            /** @constant */
            liveness: "alive";
            /** @constant */
            readiness: "ready";
            checks: components["schemas"]["HealthReadyChecks"];
            details: components["schemas"]["HealthReadyDetails"];
        } & components["schemas"]["HealthStaffStatusCoupling"];
        HealthReady: components["schemas"]["HealthReadyPublic"] | components["schemas"]["HealthReadyStaff"];
        HealthNotReadyPublic: {
            /** @constant */
            status: "not_ready";
            /** @constant */
            liveness: "alive";
            /** @constant */
            readiness: "not_ready";
            checks: components["schemas"]["HealthNotReadyChecks"];
        };
        HealthNotReadyStaff: {
            /** @constant */
            status: "not_ready";
            /** @constant */
            liveness: "alive";
            /** @constant */
            readiness: "not_ready";
            checks: components["schemas"]["HealthNotReadyChecks"];
            details: components["schemas"]["HealthNotReadyDetails"];
        } & components["schemas"]["HealthStaffStatusCoupling"];
        HealthNotReady: components["schemas"]["HealthNotReadyPublic"] | components["schemas"]["HealthNotReadyStaff"];
    };
    responses: never;
    parameters: never;
    requestBodies: never;
    headers: never;
    pathItems: never;
}
export type $defs = Record<string, never>;
export interface operations {
    browser_csrf: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description The CSRF cookie was set; the response is uncacheable. */
            204: {
                headers: {
                    [name: string]: unknown;
                };
                content?: never;
            };
            /** @description Django rejected the Host header before the operation executed. */
            400: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorEnvelope"];
                };
            };
            /** @description The CSRF route accepts only GET. */
            405: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorEnvelope"];
                };
            };
            /** @description The requested response representation is unavailable. */
            406: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorEnvelope"];
                };
            };
            /** @description The request body exceeds the environment-configured API limit. */
            413: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorEnvelope"];
                };
            };
            /** @description The client address exceeded the anonymous scope. */
            429: {
                headers: {
                    /** @description Whole seconds until the fixed or rolling admission window recovers. */
                    "Retry-After"?: number;
                    /** @description Request correlation identifier echoed by the error envelope. */
                    "X-Request-ID"?: string;
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorEnvelope"];
                };
            };
            /** @description An unexpected server failure was contained. */
            500: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorEnvelope"];
                };
            };
        };
    };
    browser_csrf_head: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description The CSRF cookie was set; the response is uncacheable. */
            204: {
                headers: {
                    [name: string]: unknown;
                };
                content?: never;
            };
            /** @description Django rejected the Host header before the operation executed. */
            400: {
                headers: {
                    [name: string]: unknown;
                };
                content?: never;
            };
            /** @description The requested response representation is unavailable. */
            406: {
                headers: {
                    [name: string]: unknown;
                };
                content?: never;
            };
            /** @description The request body exceeds the environment-configured API limit. */
            413: {
                headers: {
                    [name: string]: unknown;
                };
                content?: never;
            };
            /** @description The client address exceeded the anonymous scope. */
            429: {
                headers: {
                    /** @description Whole seconds until the fixed or rolling admission window recovers. */
                    "Retry-After"?: number;
                    /** @description Request correlation identifier echoed by the error envelope. */
                    "X-Request-ID"?: string;
                    [name: string]: unknown;
                };
                content?: never;
            };
            /** @description An unexpected server failure was contained. */
            500: {
                headers: {
                    [name: string]: unknown;
                };
                content?: never;
            };
        };
    };
    browser_login: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["TokenLogin"];
                "application/x-www-form-urlencoded": components["schemas"]["TokenLogin"];
                "multipart/form-data": components["schemas"]["TokenLogin"];
            };
        };
        responses: {
            /** @description Credentials were accepted; cookies were set. */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["UserProfile"];
                };
            };
            /** @description The request body is malformed or misses a required credential field. */
            400: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorEnvelope"];
                };
            };
            /** @description Credentials were rejected without disclosing account state. */
            401: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorEnvelope"];
                };
            };
            /** @description The CSRF token or Origin was missing, mismatched, or untrusted. Cookies are left in place so the client can fetch a fresh token and retry. */
            403: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorEnvelope"];
                };
            };
            /** @description The browser login route accepts only POST. */
            405: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorEnvelope"];
                };
            };
            /** @description The requested response representation is unavailable. */
            406: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorEnvelope"];
                };
            };
            /** @description The request body exceeds the environment-configured API limit. */
            413: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorEnvelope"];
                };
            };
            /** @description The submitted request representation is unsupported. */
            415: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorEnvelope"];
                };
            };
            /** @description The client address or account identifier exceeded its configured login rate. */
            429: {
                headers: {
                    /** @description Whole seconds until the fixed or rolling admission window recovers. */
                    "Retry-After"?: number;
                    /** @description Request correlation identifier echoed by the error envelope. */
                    "X-Request-ID"?: string;
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorEnvelope"];
                };
            };
            /** @description An unexpected server failure was contained. */
            500: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorEnvelope"];
                };
            };
            /** @description Authoritative account, login-admission, or token-persistence state is unavailable. */
            503: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorEnvelope"];
                };
            };
        };
    };
    browser_logout: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description The session ended and the cookies were cleared. */
            204: {
                headers: {
                    [name: string]: unknown;
                };
                content?: never;
            };
            /** @description Django rejected the Host header before the operation executed. */
            400: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorEnvelope"];
                };
            };
            /** @description The CSRF token or Origin was missing, mismatched, or untrusted. Cookies are left in place so the client can fetch a fresh token and retry. */
            403: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorEnvelope"];
                };
            };
            /** @description The browser logout route accepts only POST. */
            405: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorEnvelope"];
                };
            };
            /** @description The requested response representation is unavailable. */
            406: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorEnvelope"];
                };
            };
            /** @description The request body exceeds the environment-configured API limit. */
            413: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorEnvelope"];
                };
            };
            /** @description The client address or token account exceeded the authentication scope. */
            429: {
                headers: {
                    /** @description Whole seconds until the fixed or rolling admission window recovers. */
                    "Retry-After"?: number;
                    /** @description Request correlation identifier echoed by the error envelope. */
                    "X-Request-ID"?: string;
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorEnvelope"];
                };
            };
            /** @description An unexpected server failure was contained. */
            500: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorEnvelope"];
                };
            };
            /** @description Authoritative outstanding-token or blacklist state is unavailable. */
            503: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorEnvelope"];
                };
            };
        };
    };
    browser_refresh: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description The cookies were rotated; no body is returned. */
            204: {
                headers: {
                    [name: string]: unknown;
                };
                content?: never;
            };
            /** @description Django rejected the Host header before the operation executed. */
            400: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorEnvelope"];
                };
            };
            /** @description The refresh cookie is absent, expired, malformed, revoked, replayed, or has no active account. All three cookies are cleared. */
            401: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorEnvelope"];
                };
            };
            /** @description The CSRF token or Origin was missing, mismatched, or untrusted. Cookies are left in place so the client can fetch a fresh token and retry. */
            403: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorEnvelope"];
                };
            };
            /** @description The browser refresh route accepts only POST. */
            405: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorEnvelope"];
                };
            };
            /** @description The requested response representation is unavailable. */
            406: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorEnvelope"];
                };
            };
            /** @description The request body exceeds the environment-configured API limit. */
            413: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorEnvelope"];
                };
            };
            /** @description The client address or token account exceeded the authentication scope. */
            429: {
                headers: {
                    /** @description Whole seconds until the fixed or rolling admission window recovers. */
                    "Retry-After"?: number;
                    /** @description Request correlation identifier echoed by the error envelope. */
                    "X-Request-ID"?: string;
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorEnvelope"];
                };
            };
            /** @description An unexpected server failure was contained. */
            500: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorEnvelope"];
                };
            };
            /** @description Authoritative account, outstanding-token, or blacklist state is unavailable. */
            503: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorEnvelope"];
                };
            };
        };
    };
    jwt_create: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["TokenLogin"];
                "application/x-www-form-urlencoded": components["schemas"]["TokenLogin"];
                "multipart/form-data": components["schemas"]["TokenLogin"];
            };
        };
        responses: {
            /** @description Credentials were accepted and both token types are returned. */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["JWTCreateResponse"];
                };
            };
            /** @description The request body is malformed or misses a required credential field. */
            400: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorEnvelope"];
                };
            };
            /** @description Credentials were rejected without disclosing account state. */
            401: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorEnvelope"];
                };
            };
            /** @description The JSON web token create endpoint accepts only POST. */
            405: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorEnvelope"];
                };
            };
            /** @description The requested response representation is unavailable. */
            406: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorEnvelope"];
                };
            };
            /** @description The request body exceeds the environment-configured API limit. */
            413: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorEnvelope"];
                };
            };
            /** @description The submitted request representation is unsupported. */
            415: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorEnvelope"];
                };
            };
            /** @description The client address or account identifier exceeded its configured login rate. */
            429: {
                headers: {
                    /** @description Whole seconds until the fixed or rolling admission window recovers. */
                    "Retry-After"?: number;
                    /** @description Request correlation identifier echoed by the error envelope. */
                    "X-Request-ID"?: string;
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorEnvelope"];
                };
            };
            /** @description An unexpected server failure was contained. */
            500: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorEnvelope"];
                };
            };
            /** @description Authoritative account, login-admission, or token-persistence state is unavailable. */
            503: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorEnvelope"];
                };
            };
        };
    };
    jwt_refresh: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["JWTRefreshRequest"];
                "application/x-www-form-urlencoded": components["schemas"]["JWTRefreshRequest"];
                "multipart/form-data": components["schemas"]["JWTRefreshRequest"];
            };
        };
        responses: {
            /** @description The refresh credential was rotated. */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["JWTRefreshResponse"];
                };
            };
            /** @description The request body is malformed or misses the refresh field. */
            400: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorEnvelope"];
                };
            };
            /** @description The refresh token is expired, malformed, has invalid protocol claims, is revoked or blacklisted, or has no active account. */
            401: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorEnvelope"];
                };
            };
            /** @description The JSON web token refresh endpoint accepts only POST. */
            405: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorEnvelope"];
                };
            };
            /** @description The requested response representation is unavailable. */
            406: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorEnvelope"];
                };
            };
            /** @description The request body exceeds the environment-configured API limit. */
            413: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorEnvelope"];
                };
            };
            /** @description The submitted request representation is unsupported. */
            415: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorEnvelope"];
                };
            };
            /** @description The client address or token account exceeded the authentication scope. */
            429: {
                headers: {
                    /** @description Whole seconds until the fixed or rolling admission window recovers. */
                    "Retry-After"?: number;
                    /** @description Request correlation identifier echoed by the error envelope. */
                    "X-Request-ID"?: string;
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorEnvelope"];
                };
            };
            /** @description An unexpected server failure was contained. */
            500: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorEnvelope"];
                };
            };
            /** @description Authoritative account, outstanding-token, or blacklist state is unavailable. */
            503: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorEnvelope"];
                };
            };
        };
    };
    jwt_verify: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["JWTVerifyRequest"];
                "application/x-www-form-urlencoded": components["schemas"]["JWTVerifyRequest"];
                "multipart/form-data": components["schemas"]["JWTVerifyRequest"];
            };
        };
        responses: {
            /** @description The token is valid; no token contents are returned. */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": Record<string, never>;
                };
            };
            /** @description The request body is malformed or misses the token field. */
            400: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorEnvelope"];
                };
            };
            /** @description The token is expired, malformed, has invalid protocol claims, is revoked or blacklisted, or has no active account. */
            401: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorEnvelope"];
                };
            };
            /** @description The JSON web token verify endpoint accepts only POST. */
            405: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorEnvelope"];
                };
            };
            /** @description The requested response representation is unavailable. */
            406: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorEnvelope"];
                };
            };
            /** @description The request body exceeds the environment-configured API limit. */
            413: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorEnvelope"];
                };
            };
            /** @description The submitted request representation is unsupported. */
            415: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorEnvelope"];
                };
            };
            /** @description The client address or token account exceeded the authentication scope. */
            429: {
                headers: {
                    /** @description Whole seconds until the fixed or rolling admission window recovers. */
                    "Retry-After"?: number;
                    /** @description Request correlation identifier echoed by the error envelope. */
                    "X-Request-ID"?: string;
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorEnvelope"];
                };
            };
            /** @description An unexpected server failure was contained. */
            500: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorEnvelope"];
                };
            };
            /** @description Authoritative account or blacklist state is unavailable. */
            503: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorEnvelope"];
                };
            };
        };
    };
    token_login: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["TokenLogin"];
                "application/x-www-form-urlencoded": components["schemas"]["TokenLogin"];
                "multipart/form-data": components["schemas"]["TokenLogin"];
            };
        };
        responses: {
            /** @description Credentials were accepted and the account token is returned. */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["TokenResponse"];
                };
            };
            /** @description The request body is malformed or misses a required credential field. */
            400: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorEnvelope"];
                };
            };
            /** @description Credentials were rejected without disclosing account state. */
            401: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorEnvelope"];
                };
            };
            /** @description The token login endpoint accepts only POST. */
            405: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorEnvelope"];
                };
            };
            /** @description The requested response representation is unavailable. */
            406: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorEnvelope"];
                };
            };
            /** @description The request body exceeds the environment-configured API limit. */
            413: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorEnvelope"];
                };
            };
            /** @description The submitted request representation is unsupported. */
            415: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorEnvelope"];
                };
            };
            /** @description The client address or account identifier exceeded its configured login rate. */
            429: {
                headers: {
                    /** @description Whole seconds until the fixed or rolling admission window recovers. */
                    "Retry-After"?: number;
                    /** @description Request correlation identifier echoed by the error envelope. */
                    "X-Request-ID"?: string;
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorEnvelope"];
                };
            };
            /** @description An unexpected server failure was contained. */
            500: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorEnvelope"];
                };
            };
            /** @description Authoritative account, login-admission, or token-persistence state is unavailable. */
            503: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorEnvelope"];
                };
            };
        };
    };
    token_logout: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description The presented token was destroyed. */
            204: {
                headers: {
                    [name: string]: unknown;
                };
                content?: never;
            };
            /** @description Django rejected the Host header before the operation executed. */
            400: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorEnvelope"];
                };
            };
            /** @description A token was absent, malformed, unknown, or already revoked. */
            401: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorEnvelope"];
                };
            };
            /** @description The token logout endpoint accepts only POST. */
            405: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorEnvelope"];
                };
            };
            /** @description The requested response representation is unavailable. */
            406: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorEnvelope"];
                };
            };
            /** @description The request body exceeds the environment-configured API limit. */
            413: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorEnvelope"];
                };
            };
            /** @description The client address or account exceeded the authentication scope. */
            429: {
                headers: {
                    /** @description Whole seconds until the fixed or rolling admission window recovers. */
                    "Retry-After"?: number;
                    /** @description Request correlation identifier echoed by the error envelope. */
                    "X-Request-ID"?: string;
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorEnvelope"];
                };
            };
            /** @description An unexpected server failure was contained. */
            500: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorEnvelope"];
                };
            };
            /** @description Authoritative token or account state is unavailable. */
            503: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorEnvelope"];
                };
            };
        };
    };
    user_registration_or_activation: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: {
            content: {
                "application/json": components["schemas"]["UserRegistrationOrActivation"];
                "application/x-www-form-urlencoded": components["schemas"]["UserRegistrationOrActivation"];
                "multipart/form-data": components["schemas"]["UserRegistrationOrActivation"];
            };
        };
        responses: {
            /** @description The registration was accepted; the account is inactive until activation. */
            201: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["RegistrationResponse"];
                };
            };
            /** @description The submitted account-bound token was valid, unused, and consumed while the account became active. */
            204: {
                headers: {
                    [name: string]: unknown;
                };
                content?: never;
            };
            /** @description The body is malformed, misses a required field, carries an undeclared field, has mismatched confirmation, or fails email or password validation. */
            400: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorEnvelope"];
                };
            };
            /** @description The user collection accepts only POST registration or activation. */
            405: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorEnvelope"];
                };
            };
            /** @description The requested response representation is unavailable. */
            406: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorEnvelope"];
                };
            };
            /** @description The request body exceeds the environment-configured API limit. */
            413: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorEnvelope"];
                };
            };
            /** @description The submitted request representation is unsupported. */
            415: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorEnvelope"];
                };
            };
            /** @description The client address exceeded its configured registration rate. */
            429: {
                headers: {
                    /** @description Whole seconds until the fixed or rolling admission window recovers. */
                    "Retry-After"?: number;
                    /** @description Request correlation identifier echoed by the error envelope. */
                    "X-Request-ID"?: string;
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorEnvelope"];
                };
            };
            /** @description An unexpected server failure was contained. */
            500: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorEnvelope"];
                };
            };
            /** @description Authoritative account persistence or registration-admission state is unavailable. */
            503: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorEnvelope"];
                };
            };
        };
    };
    user_profile_retrieve: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description The authenticated caller's public profile. */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["UserProfile"];
                };
            };
            /** @description Django rejected the Host header before the operation executed. */
            400: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorEnvelope"];
                };
            };
            /** @description A credential was absent, malformed, expired, revoked, or inactive. */
            401: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorEnvelope"];
                };
            };
            /** @description The self-profile route supports only GET, PATCH, and DELETE. */
            405: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorEnvelope"];
                };
            };
            /** @description The requested response representation is unavailable. */
            406: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorEnvelope"];
                };
            };
            /** @description The request body exceeds the environment-configured API limit. */
            413: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorEnvelope"];
                };
            };
            /** @description The client address or account exceeded the authenticated-read scope. */
            429: {
                headers: {
                    /** @description Whole seconds until the fixed or rolling admission window recovers. */
                    "Retry-After"?: number;
                    /** @description Request correlation identifier echoed by the error envelope. */
                    "X-Request-ID"?: string;
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorEnvelope"];
                };
            };
            /** @description An unexpected server failure was contained. */
            500: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorEnvelope"];
                };
            };
            /** @description Authoritative account or credential state is unavailable. */
            503: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorEnvelope"];
                };
            };
        };
    };
    user_profile_delete: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["UserProfileDeletion"];
                "application/x-www-form-urlencoded": components["schemas"]["UserProfileDeletion"];
                "multipart/form-data": components["schemas"]["UserProfileDeletion"];
            };
        };
        responses: {
            /** @description The account and its secondary token were removed. Digest-only activation classification tombstones and detached JWT revocation metadata remain until scheduled cleanup; operational logs and backups remain under their documented retention policies. */
            204: {
                headers: {
                    [name: string]: unknown;
                };
                content?: never;
            };
            /** @description The body is malformed, misses current_password, carries another field, or supplies an incorrect current password. */
            400: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorEnvelope"];
                };
            };
            /** @description A credential was absent, malformed, expired, revoked, or inactive. */
            401: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorEnvelope"];
                };
            };
            /** @description The self-profile route supports only GET, PATCH, and DELETE. */
            405: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorEnvelope"];
                };
            };
            /** @description The requested response representation is unavailable. */
            406: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorEnvelope"];
                };
            };
            /** @description The request body exceeds the environment-configured API limit. */
            413: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorEnvelope"];
                };
            };
            /** @description The submitted request representation is unsupported. */
            415: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorEnvelope"];
                };
            };
            /** @description The client address or account exceeded the authentication scope. */
            429: {
                headers: {
                    /** @description Whole seconds until the fixed or rolling admission window recovers. */
                    "Retry-After"?: number;
                    /** @description Request correlation identifier echoed by the error envelope. */
                    "X-Request-ID"?: string;
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorEnvelope"];
                };
            };
            /** @description An unexpected server failure was contained. */
            500: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorEnvelope"];
                };
            };
            /** @description Authoritative account or credential state is unavailable. */
            503: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorEnvelope"];
                };
            };
        };
    };
    user_profile_head: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description The authenticated caller's public profile. */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content?: never;
            };
            /** @description Django rejected the Host header before the operation executed. */
            400: {
                headers: {
                    [name: string]: unknown;
                };
                content?: never;
            };
            /** @description A credential was absent, malformed, expired, revoked, or inactive. */
            401: {
                headers: {
                    [name: string]: unknown;
                };
                content?: never;
            };
            /** @description The requested response representation is unavailable. */
            406: {
                headers: {
                    [name: string]: unknown;
                };
                content?: never;
            };
            /** @description The request body exceeds the environment-configured API limit. */
            413: {
                headers: {
                    [name: string]: unknown;
                };
                content?: never;
            };
            /** @description The client address or account exceeded the authenticated-read scope. */
            429: {
                headers: {
                    /** @description Whole seconds until the fixed or rolling admission window recovers. */
                    "Retry-After"?: number;
                    /** @description Request correlation identifier echoed by the error envelope. */
                    "X-Request-ID"?: string;
                    [name: string]: unknown;
                };
                content?: never;
            };
            /** @description An unexpected server failure was contained. */
            500: {
                headers: {
                    [name: string]: unknown;
                };
                content?: never;
            };
            /** @description Authoritative account or credential state is unavailable. */
            503: {
                headers: {
                    [name: string]: unknown;
                };
                content?: never;
            };
        };
    };
    user_profile_update: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: {
            content: {
                "application/json": components["schemas"]["PatchedUserProfileUpdate"];
                "application/x-www-form-urlencoded": components["schemas"]["PatchedUserProfileUpdate"];
                "multipart/form-data": components["schemas"]["PatchedUserProfileUpdate"];
            };
        };
        responses: {
            /** @description The caller's profile after applying the submitted email change. */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["UserProfile"];
                };
            };
            /** @description The body is malformed, email is invalid or occupied, or a forbidden field was submitted. */
            400: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorEnvelope"];
                };
            };
            /** @description A credential was absent, malformed, expired, revoked, or inactive. */
            401: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorEnvelope"];
                };
            };
            /** @description The self-profile route supports only GET, PATCH, and DELETE. */
            405: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorEnvelope"];
                };
            };
            /** @description The requested response representation is unavailable. */
            406: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorEnvelope"];
                };
            };
            /** @description The request body exceeds the environment-configured API limit. */
            413: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorEnvelope"];
                };
            };
            /** @description The submitted request representation is unsupported. */
            415: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorEnvelope"];
                };
            };
            /** @description The client address or account exceeded the authentication scope. */
            429: {
                headers: {
                    /** @description Whole seconds until the fixed or rolling admission window recovers. */
                    "Retry-After"?: number;
                    /** @description Request correlation identifier echoed by the error envelope. */
                    "X-Request-ID"?: string;
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorEnvelope"];
                };
            };
            /** @description An unexpected server failure was contained. */
            500: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorEnvelope"];
                };
            };
            /** @description Authoritative account or credential state is unavailable. */
            503: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorEnvelope"];
                };
            };
        };
    };
    user_activation_resend: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["ActivationResend"];
                "application/x-www-form-urlencoded": components["schemas"]["ActivationResend"];
                "multipart/form-data": components["schemas"]["ActivationResend"];
            };
        };
        responses: {
            /** @description The resend request was accepted without disclosing whether an account matched. */
            202: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ActivationResendResponse"];
                };
            };
            /** @description The body is malformed, misses the email field, carries an undeclared field, or contains an invalid email address. */
            400: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorEnvelope"];
                };
            };
            /** @description Activation resend accepts only POST. */
            405: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorEnvelope"];
                };
            };
            /** @description The requested response representation is unavailable. */
            406: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorEnvelope"];
                };
            };
            /** @description The request body exceeds the environment-configured API limit. */
            413: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorEnvelope"];
                };
            };
            /** @description The submitted request representation is unsupported. */
            415: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorEnvelope"];
                };
            };
            /** @description The client address or account exceeded its configured activation resend rate. */
            429: {
                headers: {
                    /** @description Whole seconds until the fixed or rolling admission window recovers. */
                    "Retry-After"?: number;
                    /** @description Request correlation identifier echoed by the error envelope. */
                    "X-Request-ID"?: string;
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorEnvelope"];
                };
            };
            /** @description An unexpected server failure was contained. */
            500: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorEnvelope"];
                };
            };
            /** @description Authoritative account, token, or resend-admission state is unavailable. */
            503: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorEnvelope"];
                };
            };
        };
    };
    user_password_reset_request: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["PasswordResetRequest"];
                "application/x-www-form-urlencoded": components["schemas"]["PasswordResetRequest"];
                "multipart/form-data": components["schemas"]["PasswordResetRequest"];
            };
        };
        responses: {
            /** @description The request was accepted without disclosing account state. */
            202: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["PasswordResetResponse"];
                };
            };
            /** @description The body is malformed, misses email, carries another field, or supplies an invalid email address. */
            400: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorEnvelope"];
                };
            };
            /** @description Password reset requests accept only POST. */
            405: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorEnvelope"];
                };
            };
            /** @description The requested response representation is unavailable. */
            406: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorEnvelope"];
                };
            };
            /** @description The request body exceeds the environment-configured API limit. */
            413: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorEnvelope"];
                };
            };
            /** @description The submitted request representation is unsupported. */
            415: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorEnvelope"];
                };
            };
            /** @description The client address or recipient identity exceeded its configured reset rate. */
            429: {
                headers: {
                    /** @description Whole seconds until the fixed or rolling admission window recovers. */
                    "Retry-After"?: number;
                    /** @description Request correlation identifier echoed by the error envelope. */
                    "X-Request-ID"?: string;
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorEnvelope"];
                };
            };
            /** @description An unexpected server failure was contained. */
            500: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorEnvelope"];
                };
            };
            /** @description Authoritative account lookup or reset-admission state is unavailable. */
            503: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorEnvelope"];
                };
            };
        };
    };
    user_password_reset_confirm: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["PasswordResetConfirm"];
                "application/x-www-form-urlencoded": components["schemas"]["PasswordResetConfirm"];
                "multipart/form-data": components["schemas"]["PasswordResetConfirm"];
            };
        };
        responses: {
            /** @description The password changed, all reset links and existing identities were revoked, and a password-change notification was scheduled. */
            204: {
                headers: {
                    [name: string]: unknown;
                };
                content?: never;
            };
            /** @description The body or replacement is invalid, or the reset token is malformed, foreign, expired, or already used. */
            400: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorEnvelope"];
                };
            };
            /** @description Password reset confirmation accepts only POST. */
            405: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorEnvelope"];
                };
            };
            /** @description The requested response representation is unavailable. */
            406: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorEnvelope"];
                };
            };
            /** @description The request body exceeds the environment-configured API limit. */
            413: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorEnvelope"];
                };
            };
            /** @description The submitted request representation is unsupported. */
            415: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorEnvelope"];
                };
            };
            /** @description The client address or account exceeded its configured reset-confirm rate. */
            429: {
                headers: {
                    /** @description Whole seconds until the fixed or rolling admission window recovers. */
                    "Retry-After"?: number;
                    /** @description Request correlation identifier echoed by the error envelope. */
                    "X-Request-ID"?: string;
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorEnvelope"];
                };
            };
            /** @description An unexpected server failure was contained. */
            500: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorEnvelope"];
                };
            };
            /** @description Authoritative account, credential, token, or admission state is unavailable. */
            503: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorEnvelope"];
                };
            };
        };
    };
    user_username_reset_request: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["UsernameResetRequest"];
                "application/x-www-form-urlencoded": components["schemas"]["UsernameResetRequest"];
                "multipart/form-data": components["schemas"]["UsernameResetRequest"];
            };
        };
        responses: {
            /** @description The request was accepted without disclosing account state. */
            202: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["UsernameResetResponse"];
                };
            };
            /** @description The body is malformed, misses email, carries another field, or supplies an invalid email address. */
            400: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorEnvelope"];
                };
            };
            /** @description Username reset requests accept only POST. */
            405: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorEnvelope"];
                };
            };
            /** @description The requested response representation is unavailable. */
            406: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorEnvelope"];
                };
            };
            /** @description The request body exceeds the environment-configured API limit. */
            413: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorEnvelope"];
                };
            };
            /** @description The submitted request representation is unsupported. */
            415: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorEnvelope"];
                };
            };
            /** @description The client address or recipient identity exceeded its configured reset rate. */
            429: {
                headers: {
                    /** @description Whole seconds until the fixed or rolling admission window recovers. */
                    "Retry-After"?: number;
                    /** @description Request correlation identifier echoed by the error envelope. */
                    "X-Request-ID"?: string;
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorEnvelope"];
                };
            };
            /** @description An unexpected server failure was contained. */
            500: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorEnvelope"];
                };
            };
            /** @description Authoritative account lookup or reset-admission state is unavailable. */
            503: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorEnvelope"];
                };
            };
        };
    };
    user_username_reset_confirm: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["UsernameResetConfirm"];
                "application/x-www-form-urlencoded": components["schemas"]["UsernameResetConfirm"];
                "multipart/form-data": components["schemas"]["UsernameResetConfirm"];
            };
        };
        responses: {
            /** @description The username changed, all username-reset links were consumed, existing credentials remained valid, and a notification was scheduled. */
            204: {
                headers: {
                    [name: string]: unknown;
                };
                content?: never;
            };
            /** @description The body or replacement username is invalid or unavailable, or the reset token is malformed, foreign, expired, or already used. */
            400: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorEnvelope"];
                };
            };
            /** @description Username reset confirmation accepts only POST. */
            405: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorEnvelope"];
                };
            };
            /** @description The requested response representation is unavailable. */
            406: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorEnvelope"];
                };
            };
            /** @description The request body exceeds the environment-configured API limit. */
            413: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorEnvelope"];
                };
            };
            /** @description The submitted request representation is unsupported. */
            415: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorEnvelope"];
                };
            };
            /** @description The client address or account exceeded its configured reset-confirm rate. */
            429: {
                headers: {
                    /** @description Whole seconds until the fixed or rolling admission window recovers. */
                    "Retry-After"?: number;
                    /** @description Request correlation identifier echoed by the error envelope. */
                    "X-Request-ID"?: string;
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorEnvelope"];
                };
            };
            /** @description An unexpected server failure was contained. */
            500: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorEnvelope"];
                };
            };
            /** @description Authoritative account, token, uniqueness, or admission state is unavailable. */
            503: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorEnvelope"];
                };
            };
        };
    };
    user_password_change: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["PasswordChange"];
                "application/x-www-form-urlencoded": components["schemas"]["PasswordChange"];
                "multipart/form-data": components["schemas"]["PasswordChange"];
            };
        };
        responses: {
            /** @description The password changed and every existing credential and session was revoked, including the caller's. */
            204: {
                headers: {
                    [name: string]: unknown;
                };
                content?: never;
            };
            /** @description The body is malformed, misses a field, carries another field, supplies an incorrect current password, repeats the current password, mismatches confirmation, or fails configured password validation. */
            400: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorEnvelope"];
                };
            };
            /** @description A credential was absent, malformed, expired, revoked, or inactive. */
            401: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorEnvelope"];
                };
            };
            /** @description Password change accepts only POST. */
            405: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorEnvelope"];
                };
            };
            /** @description The requested response representation is unavailable. */
            406: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorEnvelope"];
                };
            };
            /** @description The request body exceeds the environment-configured API limit. */
            413: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorEnvelope"];
                };
            };
            /** @description The submitted request representation is unsupported. */
            415: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorEnvelope"];
                };
            };
            /** @description The client address or account exceeded the authentication scope. */
            429: {
                headers: {
                    /** @description Whole seconds until the fixed or rolling admission window recovers. */
                    "Retry-After"?: number;
                    /** @description Request correlation identifier echoed by the error envelope. */
                    "X-Request-ID"?: string;
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorEnvelope"];
                };
            };
            /** @description An unexpected server failure was contained. */
            500: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorEnvelope"];
                };
            };
            /** @description Authoritative account or credential state is unavailable. */
            503: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorEnvelope"];
                };
            };
        };
    };
    user_username_change: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["UsernameChange"];
                "application/x-www-form-urlencoded": components["schemas"]["UsernameChange"];
                "multipart/form-data": components["schemas"]["UsernameChange"];
            };
        };
        responses: {
            /** @description The username changed, existing credentials remained valid, outstanding username-reset links were consumed, and a notification was scheduled. */
            204: {
                headers: {
                    [name: string]: unknown;
                };
                content?: never;
            };
            /** @description The body is malformed, misses a field, carries another field, supplies an incorrect current password, or uses an invalid or unavailable username. */
            400: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorEnvelope"];
                };
            };
            /** @description A credential was absent, malformed, expired, revoked, or inactive. */
            401: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorEnvelope"];
                };
            };
            /** @description Username change accepts only POST. */
            405: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorEnvelope"];
                };
            };
            /** @description The requested response representation is unavailable. */
            406: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorEnvelope"];
                };
            };
            /** @description The request body exceeds the environment-configured API limit. */
            413: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorEnvelope"];
                };
            };
            /** @description The submitted request representation is unsupported. */
            415: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorEnvelope"];
                };
            };
            /** @description The client address or account exceeded the authentication scope. */
            429: {
                headers: {
                    /** @description Whole seconds until the fixed or rolling admission window recovers. */
                    "Retry-After"?: number;
                    /** @description Request correlation identifier echoed by the error envelope. */
                    "X-Request-ID"?: string;
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorEnvelope"];
                };
            };
            /** @description An unexpected server failure was contained. */
            500: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorEnvelope"];
                };
            };
            /** @description Authoritative account, credential, or uniqueness state is unavailable. */
            503: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorEnvelope"];
                };
            };
        };
    };
    health_readiness: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Every required dependency is working. */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HealthReady"];
                };
            };
            /** @description Django rejected the Host header before the operation executed. */
            400: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorEnvelope"];
                };
            };
            /** @description The health endpoint accepts only safe read methods. */
            405: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorEnvelope"];
                };
            };
            /** @description The requested representation is not available. */
            406: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorEnvelope"];
                };
            };
            /** @description An unexpected readiness failure was contained and correlated. */
            500: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorEnvelope"];
                };
            };
            /** @description At least one required dependency is unavailable. */
            503: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HealthNotReady"];
                };
            };
        };
    };
    health_readiness_head: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Every required dependency is working. */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content?: never;
            };
            /** @description Django rejected the Host header before the operation executed. */
            400: {
                headers: {
                    [name: string]: unknown;
                };
                content?: never;
            };
            /** @description The requested representation is not available. */
            406: {
                headers: {
                    [name: string]: unknown;
                };
                content?: never;
            };
            /** @description An unexpected readiness failure was contained and correlated. */
            500: {
                headers: {
                    [name: string]: unknown;
                };
                content?: never;
            };
            /** @description At least one required dependency is unavailable. */
            503: {
                headers: {
                    [name: string]: unknown;
                };
                content?: never;
            };
        };
    };
}
