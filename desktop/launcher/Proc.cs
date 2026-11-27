using System;
using System.Collections.Concurrent;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using System.Linq;
using System.Runtime.InteropServices;
using System.Text;

namespace SoShop
{
    /// <summary>
    /// Starts the bundled programs without console windows, sends their output
    /// to log files, and ties them to a Windows job object so they all die with
    /// the launcher, even when it crashes or is killed from Task Manager.
    /// </summary>
    internal static class Proc
    {
        private static readonly IntPtr Job = CreateKillOnCloseJob();
        private static readonly ConcurrentDictionary<string, TextWriter> Logs =
            new ConcurrentDictionary<string, TextWriter>(StringComparer.OrdinalIgnoreCase);

        /// <summary>Quotes one argument for the Windows command line.</summary>
        public static string Quote(string arg)
        {
            if (arg.Length > 0 && arg.IndexOfAny(new[] { ' ', '\t', '"' }) < 0) return arg;
            var sb = new StringBuilder("\"");
            int slashes = 0;
            foreach (var c in arg)
            {
                if (c == '\\') { slashes++; continue; }
                if (c == '"') sb.Append('\\', slashes * 2 + 1).Append('"');
                else sb.Append('\\', slashes).Append(c);
                slashes = 0;
            }
            return sb.Append('\\', slashes * 2).Append('"').ToString();
        }

        public static string Args(params string[] args) => string.Join(" ", args.Select(Quote));

        /// <summary>A long-running service. Output is appended to the log file.</summary>
        public static Process Start(string exe, string args, string cwd, IDictionary<string, string> env, string log)
        {
            var writer = OpenLog(log);
            writer.WriteLine($"===== {DateTime.Now:yyyy-MM-dd HH:mm:ss} {Path.GetFileName(exe)} {args}");
            var process = new Process { StartInfo = Info(exe, args, cwd, env), EnableRaisingEvents = true };
            DataReceivedEventHandler sink = (s, e) => { if (e.Data != null) writer.WriteLine(e.Data); };
            process.OutputDataReceived += sink;
            process.ErrorDataReceived += sink;
            process.Start();
            Assign(process);
            process.BeginOutputReadLine();
            process.BeginErrorReadLine();
            return process;
        }

        /// <summary>
        /// Runs a command to the end. Returns its exit code, or -1 after a timeout
        /// (the command is then killed). Set <paramref name="waitForOutput"/> to
        /// false for commands that leave a child running with our pipes open
        /// (pg_ctl start): their output may then be cut short.
        /// </summary>
        public static int Run(string exe, string args, string cwd, IDictionary<string, string> env, string log,
            int timeoutMs, StringBuilder capture = null, bool waitForOutput = true)
        {
            var writer = OpenLog(log);
            writer.WriteLine($"===== {DateTime.Now:yyyy-MM-dd HH:mm:ss} {Path.GetFileName(exe)} {args}");
            var process = new Process { StartInfo = Info(exe, args, cwd, env) };
            DataReceivedEventHandler sink = (s, e) =>
            {
                if (e.Data == null) return;
                writer.WriteLine(e.Data);
                if (capture != null) lock (capture) capture.AppendLine(e.Data);
            };
            process.OutputDataReceived += sink;
            process.ErrorDataReceived += sink;
            process.Start();
            Assign(process);
            process.BeginOutputReadLine();
            process.BeginErrorReadLine();
            if (!process.WaitForExit(timeoutMs))
            {
                KillTree(process);
                writer.WriteLine("(timed out, killed)");
                return -1;
            }
            int code = process.ExitCode;
            // Without waitForOutput the pipes may stay open in a child (the Postgres
            // server); the Process object is then left to the GC with its readers.
            if (waitForOutput)
            {
                process.WaitForExit();
                process.Dispose();
            }
            writer.WriteLine($"(exit code {code})");
            return code;
        }

        public static void KillTree(Process process)
        {
            if (process == null) return;
            try
            {
                if (process.HasExited) return;
                var taskkill = new ProcessStartInfo("taskkill.exe", $"/PID {process.Id} /T /F")
                {
                    UseShellExecute = false,
                    CreateNoWindow = true,
                };
                using (var killer = Process.Start(taskkill)) killer?.WaitForExit(15000);
                if (!process.HasExited) process.Kill();
                process.WaitForExit(5000);
            }
            catch (InvalidOperationException)
            {
                // Already gone.
            }
            catch (System.ComponentModel.Win32Exception)
            {
            }
        }

        public static bool IsRunning(Process process)
        {
            try
            {
                return process != null && !process.HasExited;
            }
            catch (InvalidOperationException)
            {
                return false;
            }
        }

        /// <summary>Starts a new log generation: the previous file becomes name.1.log.</summary>
        public static void RotateLogs()
        {
            foreach (var file in Directory.GetFiles(AppPaths.LogDir, "*.log"))
            {
                if (file.EndsWith(".1.log", StringComparison.OrdinalIgnoreCase)) continue;
                if (Path.GetFileName(file).Equals("launcher.log", StringComparison.OrdinalIgnoreCase) &&
                    new FileInfo(file).Length < 2_000_000) continue;
                try
                {
                    var old = Path.ChangeExtension(file, ".1.log");
                    if (File.Exists(old)) File.Delete(old);
                    File.Move(file, old);
                }
                catch (IOException)
                {
                    // In use by a leftover process: keep appending to it.
                }
            }
        }

        private static ProcessStartInfo Info(string exe, string args, string cwd, IDictionary<string, string> env)
        {
            var info = new ProcessStartInfo(exe, args)
            {
                UseShellExecute = false,
                CreateNoWindow = true,
                WorkingDirectory = cwd ?? Path.GetDirectoryName(exe),
                RedirectStandardOutput = true,
                RedirectStandardError = true,
                StandardOutputEncoding = Encoding.UTF8,
                StandardErrorEncoding = Encoding.UTF8,
            };
            if (env != null)
            {
                foreach (var pair in env)
                {
                    if (pair.Value == null) info.EnvironmentVariables.Remove(pair.Key);
                    else info.EnvironmentVariables[pair.Key] = pair.Value;
                }
            }
            return info;
        }

        private static TextWriter OpenLog(string path)
        {
            if (path == null) return TextWriter.Null;
            return Logs.GetOrAdd(path, p =>
            {
                var stream = new FileStream(p, FileMode.Append, FileAccess.Write, FileShare.ReadWrite | FileShare.Delete);
                return TextWriter.Synchronized(new StreamWriter(stream, new UTF8Encoding(false)) { AutoFlush = true });
            });
        }

        private static void Assign(Process process)
        {
            if (Job == IntPtr.Zero) return;
            try
            {
                AssignProcessToJobObject(Job, process.Handle);
            }
            catch (InvalidOperationException)
            {
                // Exited already.
            }
        }

        // --- Job object --------------------------------------------------------------

        private const int JobObjectExtendedLimitInformation = 9;
        private const uint JobObjectLimitKillOnJobClose = 0x2000;

        [StructLayout(LayoutKind.Sequential)]
        private struct JobBasicLimits
        {
            public long PerProcessUserTimeLimit;
            public long PerJobUserTimeLimit;
            public uint LimitFlags;
            public UIntPtr MinimumWorkingSetSize;
            public UIntPtr MaximumWorkingSetSize;
            public uint ActiveProcessLimit;
            public UIntPtr Affinity;
            public uint PriorityClass;
            public uint SchedulingClass;
        }

        [StructLayout(LayoutKind.Sequential)]
        private struct IoCounters
        {
            public ulong ReadOperationCount, WriteOperationCount, OtherOperationCount;
            public ulong ReadTransferCount, WriteTransferCount, OtherTransferCount;
        }

        [StructLayout(LayoutKind.Sequential)]
        private struct JobExtendedLimits
        {
            public JobBasicLimits BasicLimitInformation;
            public IoCounters IoInfo;
            public UIntPtr ProcessMemoryLimit;
            public UIntPtr JobMemoryLimit;
            public UIntPtr PeakProcessMemoryUsed;
            public UIntPtr PeakJobMemoryUsed;
        }

        [DllImport("kernel32.dll", CharSet = CharSet.Unicode)]
        private static extern IntPtr CreateJobObject(IntPtr attributes, string name);

        [DllImport("kernel32.dll", SetLastError = true)]
        private static extern bool SetInformationJobObject(IntPtr job, int infoClass, IntPtr info, uint length);

        [DllImport("kernel32.dll", SetLastError = true)]
        private static extern bool AssignProcessToJobObject(IntPtr job, IntPtr process);

        private static IntPtr CreateKillOnCloseJob()
        {
            var job = CreateJobObject(IntPtr.Zero, null);
            if (job == IntPtr.Zero) return IntPtr.Zero;
            var limits = new JobExtendedLimits();
            limits.BasicLimitInformation.LimitFlags = JobObjectLimitKillOnJobClose;
            int size = Marshal.SizeOf(typeof(JobExtendedLimits));
            var buffer = Marshal.AllocHGlobal(size);
            try
            {
                Marshal.StructureToPtr(limits, buffer, false);
                return SetInformationJobObject(job, JobObjectExtendedLimitInformation, buffer, (uint)size)
                    ? job
                    : IntPtr.Zero;
            }
            finally
            {
                Marshal.FreeHGlobal(buffer);
            }
        }
    }
}
