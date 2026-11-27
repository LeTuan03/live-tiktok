using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using System.Linq;
using System.Net.Http;
using System.Net.Sockets;
using System.Text;
using System.Text.RegularExpressions;
using System.Threading;

namespace SoShop
{
    internal enum State { Waiting, Starting, Running, Stopped, Failed }

    internal sealed class Service
    {
        public Service(string title) { Title = title; }

        public string Title { get; }
        public State State { get; set; } = State.Waiting;
        public string Detail { get; set; } = "Đang chờ";
        public Process Process { get; set; }
        public List<DateTime> Restarts { get; } = new List<DateTime>();
    }

    /// <summary>A failure the user can act on; the message is shown as is.</summary>
    internal class UserError : Exception
    {
        public UserError(string message) : base(message) { }
    }

    internal sealed class StartupError : UserError
    {
        public StartupError(Service service, string message) : base(message) { Service = service; }
        public Service Service { get; }
    }

    /// <summary>
    /// The whole SoShop stack on this machine: Postgres, LiveKit, the API, the
    /// agent worker and the web app, the same pieces docker-compose.yml runs,
    /// as plain Windows processes. Everything listens on 127.0.0.1 only.
    /// </summary>
    internal sealed class Stack
    {
        public const int WebPort = 3000;
        public const int ApiPort = 8000;      // also the Flow Bridge extension's websocket
        public const int LiveKitPort = 7880;
        public const int LiveKitTcpPort = 7881;
        public const int PgPort = 54329;
        public const int AgentPort = 18081;
        public const string StudioUrl = "http://localhost:3000/studio";
        private const string ApiBase = "http://127.0.0.1:8000";
        private static readonly string DatabaseUrl = $"postgresql://soshop:soshop@127.0.0.1:{PgPort}/soshop";

        public readonly Service Db = new Service("Cơ sở dữ liệu");
        public readonly Service LiveKit = new Service("Máy chủ phát live");
        public readonly Service Api = new Service("Máy chủ SoShop");
        public readonly Service Worker = new Service("AI dẫn live");
        public readonly Service Web = new Service("Giao diện Studio");
        public readonly Service Flow = new Service("Google Flow") { State = State.Stopped, Detail = "Chưa kết nối" };

        public IReadOnlyList<Service> Services => new[] { Db, LiveKit, Api, Worker, Web, Flow };

        /// <summary>Raised on any thread when a status changes.</summary>
        public event Action Changed;
        /// <summary>A sentence for the status line, raised on any thread.</summary>
        public event Action<string> Notice;

        public bool Ready { get; private set; }
        public bool StartFailed { get; private set; }
        public bool Stopping => stopping;
        public bool FlowConnected { get; private set; }
        public int FlowTabs { get; private set; }

        private readonly object gate = new object();
        private readonly string sitePassword = Guid.NewGuid().ToString("N");
        // No proxy: a system proxy without "bypass for local addresses" would swallow 127.0.0.1.
        private readonly HttpClient http =
            new HttpClient(new HttpClientHandler { UseProxy = false }) { Timeout = TimeSpan.FromSeconds(5) };
        private volatile bool stopping;
        private volatile bool paused;
        private int generation;
        private bool webSeen;
        private bool browserOpened;
        private DateTime nextFlowPoll;

        // --- Lifecycle ---------------------------------------------------------------

        /// <summary>Brings everything up, then keeps it up until Stop(). Blocks: run it on its own thread.</summary>
        public void Run()
        {
            int run = Interlocked.Increment(ref generation);
            stopping = false;
            StartFailed = false;
            try
            {
                StartAll();
            }
            catch (OperationCanceledException)
            {
                return;
            }
            catch (StartupError e)
            {
                StartFailed = true;
                Fail(e.Service, e.Message);
                return;
            }
            catch (Exception e)
            {
                StartFailed = true;
                Log.Write("startup failed: " + e);
                Say("Khởi động thất bại: " + e.Message);
                Changed?.Invoke();
                return;
            }

            while (!stopping && run == generation)
            {
                Thread.Sleep(2000);
                if (stopping || run != generation) break;
                try
                {
                    Watch();
                }
                catch (Exception e)
                {
                    Log.Write("watch: " + e);
                }
            }
        }

        /// <summary>Stops every service, the database last. Safe to call more than once.</summary>
        public void Stop()
        {
            stopping = true;
            Ready = false;
            lock (gate)
            {
                foreach (var service in new[] { Web, Worker, Api, LiveKit })
                {
                    Proc.KillTree(service.Process);
                    service.Process = null;
                    Set(service, State.Stopped, "Đã tắt");
                }
                StopPostgres();
                Set(Db, State.Stopped, "Đã tắt");
                FlowConnected = false;
                Set(Flow, State.Stopped, "Chưa kết nối");
            }
        }

        /// <summary>Stops the API, worker and web app (for a restore); the database keeps running.</summary>
        public void PauseApp()
        {
            paused = true;
            Ready = false;
            lock (gate)
            {
                foreach (var service in new[] { Web, Worker, Api })
                {
                    Proc.KillTree(service.Process);
                    service.Process = null;
                    Set(service, State.Stopped, "Tạm dừng");
                }
            }
        }

        public void ResumeApp()
        {
            lock (gate)
            {
                StartApi();
                StartWorker();
                StartWeb();
            }
            paused = false;
        }

        /// <summary>Restarts the Python services so they read new settings.</summary>
        public void RestartPython()
        {
            lock (gate)
            {
                foreach (var service in new[] { Worker, Api })
                {
                    Proc.KillTree(service.Process);
                    service.Restarts.Clear();
                }
                StartApi();
                StartWorker();
            }
        }

        private void StartAll()
        {
            Proc.RotateLogs();
            SyncExtension();
            Set(Db, State.Starting, "Đang kiểm tra");
            CheckPorts();
            StartPostgres();
            Migrate();
            lock (gate)
            {
                if (stopping) throw new OperationCanceledException(); // closed while migrating
                StartLiveKit();
                StartApi();
                StartWeb();
            }
            WaitFor(() => PortOpen(LiveKitPort), 60, LiveKit,
                "Máy chủ phát live không khởi động được. Xem logs\\livekit.log.");
            Set(LiveKit, State.Running, "Đang chạy");
            lock (gate)
            {
                if (stopping) throw new OperationCanceledException();
                StartWorker();
            }
            WaitFor(ApiHealthy, 300, Api, "Máy chủ SoShop không khởi động được. Xem logs\\api.log.");
            Set(Api, State.Running, "Đang chạy");
            WaitFor(WebHealthy, 180, Web, "Giao diện Studio không khởi động được. Xem logs\\web.log.");
            Set(Web, State.Running, "Đang chạy");
            Ready = true;
            Say("SoShop đã sẵn sàng.");
            if (Program.OpenBrowser && !browserOpened)
            {
                browserOpened = true;
                OpenUrl(StudioUrl);
            }
        }

        /// <summary>One pass of the watchdog: restart what died, refresh what the window shows.</summary>
        private void Watch()
        {
            if (paused) return;
            lock (gate)
            {
                if (paused || stopping) return;
                if (Revive(LiveKit, StartLiveKit))
                {
                    // A worker does not survive a LiveKit restart: it stays registered but fails every job.
                    Proc.KillTree(Worker.Process);
                    StartWorker();
                }
                Revive(Api, StartApi);
                Revive(Worker, StartWorker);
                Revive(Web, StartWeb);
            }

            if (PortOpen(PgPort)) Set(Db, State.Running, "Đang chạy");
            else if (!stopping && Db.State != State.Failed)
            {
                Set(Db, State.Starting, "Đang khởi động lại");
                try
                {
                    StartPostgres();
                }
                catch (StartupError e)
                {
                    Fail(Db, e.Message);
                }
            }

            Health(LiveKit, PortOpen(LiveKitPort));
            Health(Api, ApiHealthy());
            Health(Web, webSeen ? PortOpen(WebPort) : WebHealthy());
            if (Proc.IsRunning(Worker.Process))
            {
                Set(Worker, State.Running, HasGroqKey() ? "Đang chạy" : "Thiếu Groq key, bấm Cài đặt");
            }
            Ready = Api.State == State.Running && Web.State == State.Running;
            Changed?.Invoke();
            PollFlow();
        }

        /// <summary>Restarts a dead service, at most 5 times in 10 minutes. True when it restarted it.</summary>
        private bool Revive(Service service, Action start)
        {
            if (service.Process == null || Proc.IsRunning(service.Process) || service.State == State.Failed)
                return false;
            var now = DateTime.Now;
            service.Restarts.RemoveAll(t => now - t > TimeSpan.FromMinutes(10));
            if (service.Restarts.Count >= 5)
            {
                Fail(service, service.Title + " dừng liên tục. Xem thư mục nhật ký.");
                return false;
            }
            service.Restarts.Add(now);
            Log.Write($"{service.Title} exited with code {SafeExitCode(service.Process)}, restarting");
            Set(service, State.Starting, "Đang khởi động lại");
            start();
            return true;
        }

        private void Health(Service service, bool healthy)
        {
            if (healthy) Set(service, State.Running, "Đang chạy");
            else if (service.State == State.Running) Set(service, State.Starting, "Không phản hồi, đang chờ");
        }

        // --- Services ----------------------------------------------------------------

        private void StartPostgres()
        {
            var pgdata = AppPaths.PgData;
            if (!File.Exists(Path.Combine(pgdata, "PG_VERSION")))
            {
                Set(Db, State.Starting, "Đang tạo cơ sở dữ liệu (lần đầu)");
                if (Directory.Exists(pgdata)) Directory.Delete(pgdata, true); // left half-made by a failed first run
                int init = Proc.Run(PgTool("initdb"),
                    Proc.Args("-D", pgdata, "-U", "soshop", "-A", "trust", "-E", "UTF8", "--locale=C", "--no-instructions"),
                    AppPaths.PgBin, PgEnv(), AppPaths.Log("postgres-init.log"), 300_000);
                if (init != 0) throw new StartupError(Db, "Không tạo được cơ sở dữ liệu. Xem logs\\postgres-init.log.");
            }
            Set(Db, State.Starting, "Đang khởi động");
            if (!PgRunning())
            {
                // pg_ctl leaves the server running with our output pipes open, so do not wait for them.
                int start = Proc.Run(PgTool("pg_ctl"),
                    Proc.Args("start", "-D", pgdata, "-l", AppPaths.Log("postgres.log"), "-w", "-t", "120",
                        "-o", $"-p {PgPort} -c listen_addresses=127.0.0.1"),
                    AppPaths.PgBin, PgEnv(), AppPaths.Log("pg_ctl.log"), 150_000, waitForOutput: false);
                if (start != 0) throw new StartupError(Db, "Cơ sở dữ liệu không khởi động được. Xem logs\\postgres.log.");
            }
            EnsureDatabase();
            Set(Db, State.Running, "Đang chạy");
        }

        private void StopPostgres()
        {
            if (!File.Exists(Path.Combine(AppPaths.PgData, "PG_VERSION"))) return;
            Proc.Run(PgTool("pg_ctl"), Proc.Args("stop", "-D", AppPaths.PgData, "-m", "fast", "-w", "-t", "60"),
                AppPaths.PgBin, PgEnv(), AppPaths.Log("pg_ctl.log"), 90_000);
        }

        private bool PgRunning()
        {
            if (!File.Exists(Path.Combine(AppPaths.PgData, "PG_VERSION"))) return false;
            return Proc.Run(PgTool("pg_ctl"), Proc.Args("status", "-D", AppPaths.PgData),
                AppPaths.PgBin, PgEnv(), null, 30_000) == 0;
        }

        private void EnsureDatabase()
        {
            var output = new StringBuilder();
            Psql("postgres", "SELECT 1 FROM pg_database WHERE datname = 'soshop'", output);
            if (output.ToString().Split('\n').Any(line => line.Trim() == "1")) return;
            if (PgClient("createdb", "soshop") != 0)
                throw new StartupError(Db, "Không tạo được cơ sở dữ liệu soshop. Xem logs\\postgres-admin.log.");
        }

        /// <summary>Applies pending migrations; seeds the demo catalog into an empty database.</summary>
        public void Migrate(string baseline = null)
        {
            Set(Api, State.Starting, "Đang cập nhật cơ sở dữ liệu");
            var args = "-m soshop_server.db.migrate --seed-if-empty";
            if (baseline != null) args += " --baseline " + Proc.Quote(baseline);
            int code = Proc.Run(AppPaths.Python, args, AppPaths.ServerDir, PyEnv(), AppPaths.Log("migrate.log"), 600_000);
            if (code != 0) throw new StartupError(Api, "Cập nhật cơ sở dữ liệu thất bại. Xem logs\\migrate.log.");
        }

        private void StartLiveKit()
        {
            Set(LiveKit, State.Starting, "Đang khởi động");
            // --node-ip 127.0.0.1: without it the browser gets signaling but no media.
            LiveKit.Process = Proc.Start(AppPaths.LiveKit, "--dev --bind 127.0.0.1 --node-ip 127.0.0.1",
                Path.GetDirectoryName(AppPaths.LiveKit), null, AppPaths.Log("livekit.log"));
        }

        private void StartApi()
        {
            Set(Api, State.Starting, "Đang khởi động");
            Api.Process = Proc.Start(AppPaths.Python,
                $"-m uvicorn soshop_server.api.app:app --host 127.0.0.1 --port {ApiPort} --no-access-log",
                AppPaths.ServerDir, PyEnv(), AppPaths.Log("api.log"));
        }

        private void StartWorker()
        {
            Set(Worker, State.Starting, "Đang khởi động");
            Worker.Process = Proc.Start(AppPaths.Python, "-m soshop_server.agent.main start",
                AppPaths.ServerDir, PyEnv(), AppPaths.Log("worker.log"));
        }

        private void StartWeb()
        {
            Set(Web, State.Starting, "Đang khởi động");
            webSeen = false;
            var env = new Dictionary<string, string>
            {
                ["PORT"] = WebPort.ToString(),
                ["HOSTNAME"] = "127.0.0.1",
                ["NODE_ENV"] = "production",
                ["NEXT_TELEMETRY_DISABLED"] = "1",
                ["SITE_PASSWORD"] = sitePassword,
                ["NODE_OPTIONS"] = null,
            };
            Web.Process = Proc.Start(AppPaths.Node, "server.js", AppPaths.WebDir, env, AppPaths.Log("web.log"));
        }

        // --- Backup support ------------------------------------------------------------

        public void DumpDatabase(string file)
        {
            if (PgClient("pg_dump", "-d", "soshop", "-Fc", "-f", file) != 0)
                throw new UserError("Không xuất được cơ sở dữ liệu. Xem logs\\postgres-admin.log.");
        }

        /// <summary>Replaces the soshop database with a pg_dump custom-format file.</summary>
        public void RestoreDatabase(string file)
        {
            if (PgClient("dropdb", "--if-exists", "--force", "soshop") != 0 || PgClient("createdb", "soshop") != 0)
                throw new UserError("Không làm mới được cơ sở dữ liệu. Xem logs\\postgres-admin.log.");
            if (PgClient("pg_restore", "--no-owner", "--no-privileges", "-d", "soshop", file) != 0)
                throw new UserError("Không nạp được dữ liệu từ bản sao lưu. Xem logs\\postgres-admin.log.");
        }

        private int PgClient(string tool, params string[] args)
        {
            var all = new List<string> { "-h", "127.0.0.1", "-p", PgPort.ToString(), "-U", "soshop" };
            all.AddRange(args);
            return Proc.Run(PgTool(tool), Proc.Args(all.ToArray()), AppPaths.PgBin, PgEnv(),
                AppPaths.Log("postgres-admin.log"), 3_600_000);
        }

        private void Psql(string database, string sql, StringBuilder output)
        {
            Proc.Run(PgTool("psql"),
                Proc.Args("-h", "127.0.0.1", "-p", PgPort.ToString(), "-U", "soshop", "-d", database, "-tAc", sql),
                AppPaths.PgBin, PgEnv(), AppPaths.Log("postgres-admin.log"), 60_000, output);
        }

        // --- Environment ---------------------------------------------------------------

        private static string PgTool(string name) => Path.Combine(AppPaths.PgBin, name + ".exe");

        private static Dictionary<string, string> PgEnv()
        {
            var env = new Dictionary<string, string>();
            foreach (var name in new[] { "PGDATA", "PGHOST", "PGPORT", "PGUSER", "PGPASSWORD", "PGDATABASE", "PGSERVICE", "PGOPTIONS" })
                env[name] = null;
            env["PGCLIENTENCODING"] = "UTF8";
            env["PATH"] = AppPaths.PgBin + ";" + Environment.GetEnvironmentVariable("PATH");
            return env;
        }

        /// <summary>The Python services' environment: the user's soshop.env, then the wiring of this machine.</summary>
        private Dictionary<string, string> PyEnv()
        {
            var env = new Dictionary<string, string>();
            foreach (var pair in new EnvFile(AppPaths.EnvFile).All())
                if (pair.Value.Length > 0) env[pair.Key] = pair.Value;
            foreach (var name in new[] { "PYTHONHOME", "PYTHONSTARTUP", "VIRTUAL_ENV", "CONDA_PREFIX" })
                env[name] = null;
            var pythonDir = Path.GetDirectoryName(AppPaths.Python);
            env["PYTHONPATH"] = Path.Combine(AppPaths.ServerDir, "src");
            env["PYTHONIOENCODING"] = "utf-8";
            env["PYTHONUNBUFFERED"] = "1";
            env["PYTHONNOUSERSITE"] = "1";
            env["PATH"] = string.Join(";", AppPaths.FfmpegDir, pythonDir, Path.Combine(pythonDir, "Scripts"),
                Environment.GetEnvironmentVariable("PATH"));
            env["DATABASE_URL"] = DatabaseUrl;
            env["LIVEKIT_URL"] = "ws://127.0.0.1:" + LiveKitPort;
            env["LIVEKIT_API_KEY"] = "devkey";
            env["LIVEKIT_API_SECRET"] = "secret";
            env["DATA_DIR"] = AppPaths.MediaDir;
            env["MEDIA_STORAGE"] = "local";
            env["SITE_PASSWORD"] = sitePassword;
            env["CORS_ALLOW_ORIGINS"] = "http://localhost:3000,http://127.0.0.1:3000";
            env["ENVIRONMENT"] = "development";
            env["AGENT_HTTP_HOST"] = "127.0.0.1";
            env["AGENT_HTTP_PORT"] = AgentPort.ToString();
            env["HF_HOME"] = Path.Combine(AppPaths.DataDir, "hf");
            env["OTEL_ENABLED"] = "false";
            return env;
        }

        public static bool HasGroqKey() => new EnvFile(AppPaths.EnvFile).Get("GROQ_API_KEY").Length > 0;

        /// <summary>Keeps a copy of the Chrome extension in the data folder, so Chrome's "Load unpacked" path survives updates.</summary>
        private static void SyncExtension()
        {
            if (!Directory.Exists(AppPaths.BundledExtension)) return;
            try
            {
                Directory.CreateDirectory(AppPaths.ExtensionDir);
                foreach (var file in Directory.GetFiles(AppPaths.BundledExtension))
                    File.Copy(file, Path.Combine(AppPaths.ExtensionDir, Path.GetFileName(file)), true);
            }
            catch (IOException e)
            {
                Log.Write("extension copy failed: " + e.Message);
            }
        }

        // --- Probes --------------------------------------------------------------------

        private void CheckPorts()
        {
            var wanted = new List<KeyValuePair<int, string>>
            {
                new KeyValuePair<int, string>(WebPort, "Giao diện Studio"),
                new KeyValuePair<int, string>(ApiPort, "Máy chủ SoShop"),
                new KeyValuePair<int, string>(LiveKitPort, "Máy chủ phát live"),
                new KeyValuePair<int, string>(LiveKitTcpPort, "Máy chủ phát live"),
                new KeyValuePair<int, string>(AgentPort, "AI dẫn live"),
            };
            // Our own Postgres left running by a crash is fine: StartPostgres reuses it.
            if (!PgRunning()) wanted.Add(new KeyValuePair<int, string>(PgPort, "Cơ sở dữ liệu"));

            var busy = wanted.Where(w => PortOpen(w.Key)).ToList();
            if (busy.Count == 0) return;

            // Leftovers of an earlier SoShop run hold their ports: end them.
            var owners = ListeningPids();
            foreach (var port in busy)
            {
                if (!owners.TryGetValue(port.Key, out var pid)) continue;
                var path = ProcessPath(pid);
                if (path != null && path.StartsWith(AppPaths.AppDir, StringComparison.OrdinalIgnoreCase))
                {
                    Log.Write($"ending leftover {path} (pid {pid}) on port {port.Key}");
                    try { Proc.KillTree(Process.GetProcessById(pid)); } catch (ArgumentException) { }
                }
            }
            Thread.Sleep(500);
            busy = busy.Where(w => PortOpen(w.Key)).ToList();
            if (busy.Count == 0) return;

            owners = ListeningPids();
            var parts = busy.Select(w =>
            {
                var who = owners.TryGetValue(w.Key, out var pid) ? ProcessName(pid) : null;
                return who != null ? $"{w.Key} ({who})" : w.Key.ToString();
            });
            throw new StartupError(Db,
                "Các cổng sau đang bị chương trình khác dùng: " + string.Join(", ", parts) +
                ". Hãy tắt chương trình đó (ví dụ SoShop bản Docker hoặc một máy chủ web khác) rồi bấm Thử khởi động lại.");
        }

        public static bool PortOpen(int port)
        {
            using (var client = new TcpClient())
            {
                try
                {
                    var pending = client.BeginConnect("127.0.0.1", port, null, null);
                    if (!pending.AsyncWaitHandle.WaitOne(400)) return false;
                    client.EndConnect(pending);
                    return true;
                }
                catch (SocketException)
                {
                    return false;
                }
            }
        }

        private bool ApiHealthy() => Get(ApiBase + "/healthz", out var status, out _) && status < 300;

        private bool WebHealthy()
        {
            if (!Get("http://127.0.0.1:" + WebPort + "/studio", out var status, out _) || status >= 500) return false;
            webSeen = true;
            return true;
        }

        private void PollFlow()
        {
            if (DateTime.Now < nextFlowPoll) return;
            nextFlowPoll = DateTime.Now.AddSeconds(4);
            if (Api.State != State.Running || !Get(ApiBase + "/promo/flow/status", out var status, out var body) || status != 200)
            {
                FlowConnected = false;
                Set(Flow, State.Stopped, "Chưa kết nối");
                return;
            }
            FlowConnected = Regex.IsMatch(body, "\"connected\"\\s*:\\s*true");
            var tabs = Regex.Match(body, "\"flow_tabs\"\\s*:\\s*(\\d+)");
            FlowTabs = tabs.Success ? int.Parse(tabs.Groups[1].Value) : 0;
            if (!FlowConnected) Set(Flow, State.Stopped, "Chưa kết nối");
            else Set(Flow, State.Running, FlowTabs > 0 ? $"Đã kết nối, {FlowTabs} tab Flow" : "Đã kết nối Chrome");
        }

        private bool Get(string url, out int status, out string body)
        {
            status = 0;
            body = "";
            try
            {
                using (var response = http.GetAsync(url).GetAwaiter().GetResult())
                {
                    status = (int)response.StatusCode;
                    body = response.Content.ReadAsStringAsync().GetAwaiter().GetResult();
                    return true;
                }
            }
            catch (HttpRequestException)
            {
                return false;
            }
            catch (System.Threading.Tasks.TaskCanceledException)
            {
                return false; // timed out
            }
        }

        /// <summary>Listening TCP ports on IPv4 mapped to their process id, from netstat.</summary>
        private static Dictionary<int, int> ListeningPids()
        {
            var output = new StringBuilder();
            Proc.Run("netstat.exe", "-ano -p tcp", null, null, null, 15_000, output);
            var result = new Dictionary<int, int>();
            foreach (var line in output.ToString().Split('\n'))
            {
                var cols = line.Split(new[] { ' ', '\t', '\r' }, StringSplitOptions.RemoveEmptyEntries);
                // Proto, local, foreign, state (localized), pid. A listener's foreign address is 0.0.0.0:0.
                if (cols.Length < 5 || cols[0] != "TCP" || cols[2] != "0.0.0.0:0") continue;
                var colon = cols[1].LastIndexOf(':');
                if (colon < 0 || !int.TryParse(cols[1].Substring(colon + 1), out var port)) continue;
                if (int.TryParse(cols[cols.Length - 1], out var pid)) result[port] = pid;
            }
            return result;
        }

        private static string ProcessPath(int pid)
        {
            try
            {
                using (var p = Process.GetProcessById(pid)) return p.MainModule?.FileName;
            }
            catch (Exception e) when (e is ArgumentException || e is InvalidOperationException ||
                                      e is System.ComponentModel.Win32Exception)
            {
                return null;
            }
        }

        private static string ProcessName(int pid)
        {
            try
            {
                using (var p = Process.GetProcessById(pid)) return p.ProcessName + ".exe";
            }
            catch (Exception e) when (e is ArgumentException || e is InvalidOperationException)
            {
                return null;
            }
        }

        private void WaitFor(Func<bool> ready, int seconds, Service service, string failure)
        {
            var until = DateTime.Now.AddSeconds(seconds);
            while (!ready())
            {
                if (stopping) throw new OperationCanceledException();
                if (service.Process != null && !Proc.IsRunning(service.Process)) throw new StartupError(service, failure);
                if (DateTime.Now > until) throw new StartupError(service, failure);
                Thread.Sleep(1000);
            }
        }

        private static int SafeExitCode(Process process)
        {
            try
            {
                return process.ExitCode;
            }
            catch (InvalidOperationException)
            {
                return -1;
            }
        }

        // --- Status ----------------------------------------------------------------------

        private void Set(Service service, State state, string detail)
        {
            if (service.State == state && service.Detail == detail) return;
            service.State = state;
            service.Detail = detail;
            Changed?.Invoke();
        }

        private void Fail(Service service, string message)
        {
            Log.Write("failed: " + message);
            if (service != null) Set(service, State.Failed, "Lỗi");
            Say(message);
            Changed?.Invoke();
        }

        private void Say(string message) => Notice?.Invoke(message);

        public static void OpenUrl(string url)
        {
            try
            {
                Process.Start(new ProcessStartInfo(url) { UseShellExecute = true })?.Dispose();
            }
            catch (System.ComponentModel.Win32Exception e)
            {
                Log.Write("could not open " + url + ": " + e.Message);
            }
        }
    }
}
