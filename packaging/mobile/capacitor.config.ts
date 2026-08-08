import type { CapacitorConfig } from "@capacitor/cli";

/**
 * Mindora — native shell configuration.
 *
 * The whole app is bundled into the binary (webDir below), so it launches and
 * runs with no network at all. That matters for two reasons: it is what makes
 * the app usable on a phone with no signal, and it is the single strongest
 * argument against an App Store 4.2 "this is just a website" rejection.
 *
 * appId must match the bundle identifier you register with Apple and the
 * package name you reserve on Google Play. Once an app is published, neither
 * store lets you change it — pick it before your first upload.
 */
const config: CapacitorConfig = {
  appId: "com.mindora.learn",
  appName: "Mindora",
  webDir: "www",

  // Served from a real origin (https://localhost on iOS, http://localhost on
  // Android) rather than file://. The YouTube IFrame Player API needs an
  // http(s) parent origin for its postMessage handshake — on file:// the
  // origin is "null" and watch-progress tracking silently stops working.
  server: {
    androidScheme: "http",
    iosScheme: "https",
    hostname: "localhost",
  },

  android: {
    // Google Play requires 64-bit; Gradle handles that. This only stops the
    // WebView from being debuggable in a shipped release build.
    webContentsDebuggingEnabled: false,
    allowMixedContent: false,
  },

  ios: {
    contentInset: "always",
    limitsNavigationsToAppBoundDomains: false,
    scrollEnabled: true,
  },

  plugins: {
    SplashScreen: {
      launchShowDuration: 900,
      launchAutoHide: true,
      backgroundColor: "#08060fff",
      androidSplashResourceName: "splash",
      androidScaleType: "CENTER_CROP",
      showSpinner: false,
      splashFullScreen: true,
      splashImmersive: false,
    },
    StatusBar: {
      style: "DARK",
      backgroundColor: "#08060f",
      overlaysWebView: false,
    },
  },
};

export default config;
