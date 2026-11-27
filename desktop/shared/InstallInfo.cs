using System;
using System.IO;
using System.Linq;
using System.Runtime.InteropServices;
using Microsoft.Win32;

namespace SoShop
{
    /// <summary>
    /// Install and data locations, shortcuts and the "Installed apps" entry.
    /// Shared by the setup (which installs) and the launcher (which uninstalls).
    /// Everything is per user: no admin rights, no UAC prompt.
    /// </summary>
    internal static class InstallInfo
    {
        public const string ProductName = "SoShop";
        public const string LauncherExe = "SoShop.exe";
        /// <summary>Held by a running launcher. The setup and the uninstaller check it.</summary>
        public const string MutexName = @"Local\SoShop.Launcher";
        /// <summary>A second launcher sets this to bring the running window forward.</summary>
        public const string ShowEventName = @"Local\SoShop.Launcher.Show";

        private const string UninstallKey = @"Software\Microsoft\Windows\CurrentVersion\Uninstall\SoShop";

        public static bool IsAscii(string text) => text.All(c => c < 128);

        /// <summary>
        /// %LOCALAPPDATA%\Programs\SoShop. Postgres on Windows cannot use paths
        /// outside the ANSI code page, so a profile folder such as
        /// C:\Users\Nguyễn falls back to C:\SoShop.
        /// </summary>
        public static string DefaultInstallDir()
        {
            var local = Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData);
            var dir = Path.Combine(local, "Programs", ProductName);
            return IsAscii(dir) ? dir : @"C:\SoShop";
        }

        /// <summary>%LOCALAPPDATA%\SoShop, kept when the app is updated or reinstalled.</summary>
        public static string DefaultDataDir()
        {
            var local = Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData);
            var dir = Path.Combine(local, ProductName);
            return IsAscii(dir) ? dir : @"C:\SoShop-data";
        }

        public static string StartMenuShortcut =>
            Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.Programs), ProductName + ".lnk");

        public static string DesktopShortcut =>
            Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.DesktopDirectory), ProductName + ".lnk");

        public static bool IsLauncherRunning()
        {
            if (!System.Threading.Mutex.TryOpenExisting(MutexName, out var mutex)) return false;
            mutex.Dispose();
            return true;
        }

        public static void CreateShortcut(string linkPath, string target, string description)
        {
            var shellType = Type.GetTypeFromProgID("WScript.Shell");
            if (shellType == null) return;
            dynamic shell = Activator.CreateInstance(shellType);
            try
            {
                dynamic link = shell.CreateShortcut(linkPath);
                link.TargetPath = target;
                link.WorkingDirectory = Path.GetDirectoryName(target);
                link.Description = description;
                link.IconLocation = target + ",0";
                link.Save();
            }
            finally
            {
                Marshal.FinalReleaseComObject(shell);
            }
        }

        public static void WriteUninstallEntry(string installDir, string version, long sizeKb)
        {
            var exe = Path.Combine(installDir, LauncherExe);
            using (var key = Registry.CurrentUser.CreateSubKey(UninstallKey))
            {
                if (key == null) return;
                key.SetValue("DisplayName", ProductName);
                key.SetValue("DisplayVersion", version);
                key.SetValue("Publisher", ProductName);
                key.SetValue("DisplayIcon", exe);
                key.SetValue("InstallLocation", installDir);
                key.SetValue("UninstallString", "\"" + exe + "\" --uninstall");
                key.SetValue("NoModify", 1, RegistryValueKind.DWord);
                key.SetValue("NoRepair", 1, RegistryValueKind.DWord);
                key.SetValue("EstimatedSize", (int)Math.Min(int.MaxValue, sizeKb), RegistryValueKind.DWord);
            }
        }

        /// <summary>Where an earlier setup installed SoShop, or null.</summary>
        public static string InstalledDir()
        {
            using (var key = Registry.CurrentUser.OpenSubKey(UninstallKey))
            {
                var dir = key?.GetValue("InstallLocation") as string;
                return !string.IsNullOrEmpty(dir) && File.Exists(Path.Combine(dir, LauncherExe)) ? dir : null;
            }
        }

        public static void RemoveUninstallEntry()
        {
            Registry.CurrentUser.DeleteSubKeyTree(UninstallKey, false);
        }
    }
}
