using System;
using System.IO;
using System.Text;

namespace SoShop
{
    /// <summary>
    /// Program files sit next to SoShop.exe (app\...). User data (database,
    /// videos, settings, logs) lives in a separate folder that survives
    /// updates and reinstalls.
    /// </summary>
    internal static class AppPaths
    {
        public static string ProgramDir { get; private set; }
        public static string AppDir { get; private set; }
        public static string DataDir { get; private set; }

        public static string EnvFile => Path.Combine(DataDir, "soshop.env");
        public static string PgData => Path.Combine(DataDir, "pgdata");
        public static string MediaDir => Path.Combine(DataDir, "data");
        public static string LogDir => Path.Combine(DataDir, "logs");
        public static string TempDir => Path.Combine(DataDir, "tmp");
        public static string ExtensionDir => Path.Combine(DataDir, "flow_extension");

        public static string Python => Path.Combine(AppDir, "python", "python.exe");
        public static string ServerDir => Path.Combine(AppDir, "server");
        public static string Node => Path.Combine(AppDir, "node", "node.exe");
        public static string WebDir => Path.Combine(AppDir, "web");
        public static string PgBin => Path.Combine(AppDir, "pgsql", "bin");
        public static string LiveKit => Path.Combine(AppDir, "livekit", "livekit-server.exe");
        public static string FfmpegDir => Path.Combine(AppDir, "ffmpeg");
        public static string BundledExtension => Path.Combine(AppDir, "flow_extension");
        public static string EnvTemplate => Path.Combine(AppDir, "soshop.env.example");

        public static string Version { get; private set; } = "dev";

        public static void Init(string dataOverride)
        {
            ProgramDir = AppDomain.CurrentDomain.BaseDirectory.TrimEnd('\\');
            AppDir = Path.Combine(ProgramDir, "app");
            DataDir = string.IsNullOrWhiteSpace(dataOverride)
                ? InstallInfo.DefaultDataDir()
                : Path.GetFullPath(dataOverride).TrimEnd('\\');
            foreach (var dir in new[] { DataDir, MediaDir, LogDir, TempDir })
                Directory.CreateDirectory(dir);
            var versionFile = Path.Combine(AppDir, "version.txt");
            if (File.Exists(versionFile)) Version = File.ReadAllText(versionFile).Trim();
        }

        public static string Log(string name) => Path.Combine(LogDir, name);
    }

    /// <summary>The launcher's own log, logs\launcher.log.</summary>
    internal static class Log
    {
        private static readonly object Gate = new object();

        public static void Write(string line)
        {
            try
            {
                lock (Gate)
                {
                    File.AppendAllText(AppPaths.Log("launcher.log"),
                        DateTime.Now.ToString("yyyy-MM-dd HH:mm:ss ") + line + Environment.NewLine,
                        new UTF8Encoding(false));
                }
            }
            catch (IOException)
            {
                // Logging must never take the launcher down.
            }
            catch (UnauthorizedAccessException)
            {
            }
        }
    }
}
