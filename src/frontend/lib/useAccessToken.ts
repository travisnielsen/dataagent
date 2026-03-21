"use client";

import { InteractionRequiredAuthError } from "@azure/msal-browser";
import { useIsAuthenticated, useMsal } from "@azure/msal-react";
import { useCallback, useEffect, useState } from "react";
import { apiRequest, silentRedirectUri } from "./msalConfig";

/**
 * Hook to acquire an access token for the backend API.
 * Returns the access token, loading state, and error if any.
 */
export function useAccessToken() {
  const { instance, accounts } = useMsal();
  const isAuthenticated = useIsAuthenticated();
  const [accessToken, setAccessToken] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<Error | null>(null);

  const acquireToken = useCallback(async () => {
    if (!isAuthenticated || accounts.length === 0) {
      setAccessToken(null);
      return null;
    }

    setIsLoading(true);
    setError(null);

    console.log("Acquiring token for scopes:", apiRequest.scopes);

    try {
      // Try to acquire token for the API scope first
      const response = await instance.acquireTokenSilent({
        ...apiRequest,
        account: accounts[0],
        redirectUri: silentRedirectUri,
      });
      console.log("Token acquired successfully for API scope");
      setAccessToken(response.accessToken);
      return response.accessToken;
    } catch (silentError) {
      console.warn("Silent token acquisition failed, trying interactive fallback:", silentError);

      try {
        const response = await instance.acquireTokenPopup(apiRequest);
        console.log("Token acquired via popup fallback");
        setAccessToken(response.accessToken);
        return response.accessToken;
      } catch (interactiveError) {
        // Preserve the original interaction-required error when available.
        const finalError =
          silentError instanceof InteractionRequiredAuthError
            ? silentError
            : (interactiveError as Error);
        console.error("Interactive token acquisition failed:", interactiveError);
        setError(finalError);
        setAccessToken(null);
        return null;
      }
    } finally {
      setIsLoading(false);
    }
  }, [instance, accounts, isAuthenticated]);

  // Acquire token on mount and when auth state changes
  useEffect(() => {
    acquireToken();
  }, [acquireToken]);

  return {
    accessToken,
    isLoading,
    error,
    acquireToken, // Expose this to manually refresh the token if needed
  };
}
