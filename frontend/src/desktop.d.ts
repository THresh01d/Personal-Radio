interface Window {
  personalRadioDesktop?: {
    openChatGPTAuthorization: (url: string) => Promise<void>;
    checkForUpdates?: () => Promise<void>;
    getDesktopVersion?: () => Promise<string>;
  };
}
