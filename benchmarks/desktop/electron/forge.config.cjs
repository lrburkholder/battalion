module.exports = {
  packagerConfig: {
    asar: true,
    executableName: "BattalionElectronBenchmark",
  },
  makers: [
    {
      name: "@electron-forge/maker-zip",
      platforms: ["win32", "darwin", "linux"],
    },
  ],
};

