"use client";

import { loginRequest, msalConfig } from "@/lib/msalConfig";
import {
  AuthenticationResult,
  BrowserAuthError,
  EventMessage,
  EventType,
  PublicClientApplication,
} from "@azure/msal-browser";
import { MsalProvider } from "@azure/msal-react";
import { useEffect, useRef, useState } from "react";

export function MsalAuthProvider({ children }: { children: React.ReactNode }) {
  const [isInitialized, setIsInitialized] = useState(false);
  const msalInstanceRef = useRef<PublicClientApplication | null>(null);
  const initStartedRef = useRef(false);

  useEffect(() => {
    if (initStartedRef.current) {
      return;
    }
    initStartedRef.current = true;

    const initializeMsal = async () => {
      // Create instance lazily on the client to avoid SSR "window is not defined" errors
      if (!msalInstanceRef.current) {
        msalInstanceRef.current = new PublicClientApplication(msalConfig);
      }
      const msalInstance = msalInstanceRef.current;

      await msalInstance.initialize();

      // Handle redirect response
      try {
        await msalInstance.handleRedirectPromise();
      } catch (error) {
        // This can happen in local/dev when no redirect request is cached.
        // It is recoverable and should not crash startup.
        if (
          error instanceof BrowserAuthError &&
          error.errorCode === "no_token_request_cache_error"
        ) {
          console.warn("MSAL redirect cache miss during startup, continuing.", error);
        } else {
          throw error;
        }
      }

      // Set active account if there is one
      const accounts = msalInstance.getAllAccounts();
      if (accounts.length > 0) {
        msalInstance.setActiveAccount(accounts[0]);
        setIsInitialized(true);
      } else {
        // No accounts - trigger sign-in automatically
        msalInstance.loginRedirect(loginRequest);
        // Don't set initialized - we're redirecting away
        return;
      }

      // Listen for sign-in events
      msalInstance.addEventCallback((event: EventMessage) => {
        if (event.eventType === EventType.LOGIN_SUCCESS && event.payload) {
          const payload = event.payload as AuthenticationResult;
          msalInstance.setActiveAccount(payload.account);
        }
      });
    };

    initializeMsal().catch((error) => {
      console.error("Failed to initialize MSAL provider", error);
      // Avoid blank screen on init failure; downstream auth flow can still recover.
      setIsInitialized(true);
    });
  }, []);

  if (!isInitialized || !msalInstanceRef.current) {
    return null; // Or a loading spinner
  }

  return (
    <MsalProvider instance={msalInstanceRef.current}>
      {children}
    </MsalProvider>
  );
}
