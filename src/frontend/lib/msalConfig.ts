import { Configuration, LogLevel } from "@azure/msal-browser";

const clientId = process.env.NEXT_PUBLIC_AZURE_AD_CLIENT_ID || "YOUR_CLIENT_ID";
const configuredTenantIds = [
  ...new Set(
    (process.env.NEXT_PUBLIC_AZURE_AD_TENANT_IDS ||
      process.env.NEXT_PUBLIC_AZURE_AD_TENANT_ID ||
      "")
      .split(",")
      .map((tenantId) => tenantId.trim())
      .filter(Boolean),
  ),
];
const authorityTenant =
  configuredTenantIds.length > 1 ? "organizations" : configuredTenantIds[0] || "common";
const origin = typeof window !== "undefined" ? window.location.origin : "http://localhost:3000";

// Use a dedicated static page for hidden iframe token renewal.
// This avoids CSP/frame header conflicts on the main app shell.
export const silentRedirectUri = `${origin}/auth/silent`;

/**
 * MSAL Configuration
 *
 * To configure this for your Azure AD tenant(s):
 * 1. Register an app in Azure AD (Portal > App registrations > New registration)
 * 2. Set the redirect URI to http://localhost:3000 (for development)
 * 3. Copy the Application (client) ID and approved Directory (tenant) ID values
 * 4. Replace the values below or set environment variables
 */

export const msalConfig: Configuration = {
  auth: {
    clientId,
    // Use the multi-tenant organizations authority when more than one tenant is approved.
    authority: `https://login.microsoftonline.com/${authorityTenant}`,
    redirectUri: origin,
    postLogoutRedirectUri: origin,
  },
  cache: {
    cacheLocation: "localStorage", // Use localStorage for persistence across tabs/sessions
  },
  system: {
    loggerOptions: {
      loggerCallback: (level, message, containsPii) => {
        if (containsPii) return;
        switch (level) {
          case LogLevel.Error:
            console.error(message);
            break;
          case LogLevel.Warning:
            console.warn(message);
            break;
          case LogLevel.Info:
            console.info(message);
            break;
          case LogLevel.Verbose:
            console.debug(message);
            break;
        }
      },
      logLevel: LogLevel.Warning,
    },
  },
};

// Scopes for the initial login - add more as needed
export const loginRequest = {
  scopes: ["User.Read"],
};

// Scopes for calling the backend API
// Option 1: If you've exposed an API scope in Azure Portal, use:
//   `api://${clientId}/access_as_user`
// Option 2: If you haven't set up API scopes, use the client ID directly
//   which will return an access token for the application itself
export const apiRequest = {
  scopes: [
    // Use a dedicated API scope when configured, otherwise default to api://<client-id>/access_as_user.
    process.env.NEXT_PUBLIC_AZURE_AD_API_SCOPE || `api://${clientId}/access_as_user`,
  ],
};

// Scopes for Microsoft Graph API calls
export const graphConfig = {
  graphMeEndpoint: "https://graph.microsoft.com/v1.0/me",
};
