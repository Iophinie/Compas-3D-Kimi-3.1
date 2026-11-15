using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Drawing;
using System.IO;
using System.Runtime.InteropServices;
using System.Text;
using System.Threading;
using System.Windows.Forms;
using Microsoft.Web.WebView2.Core;
using Microsoft.Web.WebView2.WinForms;

namespace KompasChatPanel
{
    internal static class Program
    {
        [STAThread]
        private static void Main(string[] args)
        {
            bool created;
            using (var mutex = new Mutex(true, @"Local\KompasChatPanel", out created))
            {
                if (!created)
                {
                    FocusExisting();
                    return;
                }
                TryEnableDpiAwareness();
                Application.EnableVisualStyles();
                Application.SetCompatibleTextRenderingDefault(false);
                var options = Options.Parse(args);
                Log("panel start");
                Application.Run(new PanelForm(options));
                Log("panel exit");
            }
        }

        private static void TryEnableDpiAwareness()
        {
            try { SetProcessDpiAwarenessContext(new IntPtr(-4)); } catch { }
        }

        internal static void Log(string message)
        {
            try
            {
                File.AppendAllText(Path.Combine(AppDomain.CurrentDomain.BaseDirectory, "panel.log"),
                    DateTime.Now.ToString("s") + " " + message + Environment.NewLine);
            }
            catch { }
        }

        private static void FocusExisting()
        {
            IntPtr hwnd = FindWindow(null, "Kimi for KOMPAS");
            if (hwnd != IntPtr.Zero)
            {
                ShowWindow(hwnd, SW_RESTORE);
                SetForegroundWindow(hwnd);
            }
        }

        [DllImport("user32.dll", CharSet = CharSet.Unicode)]
        private static extern IntPtr FindWindow(string cls, string title);
        [DllImport("user32.dll")]
        private static extern bool ShowWindow(IntPtr hwnd, int cmd);
        [DllImport("user32.dll")]
        private static extern bool SetForegroundWindow(IntPtr hwnd);
        [DllImport("user32.dll")]
        private static extern bool SetProcessDpiAwarenessContext(IntPtr value);
        private const int SW_RESTORE = 9;
    }

    internal sealed class Options
    {
        public string Url = "http://127.0.0.1:8765/";
        public int Width = 440;
        public string DataDir = "";
        public string TestMessage = "";
        public string EvalScript = "";
        public bool Smoke;

        public static Options Parse(string[] args)
        {
            var options = new Options();
            for (int i = 0; i < args.Length; i++)
            {
                string arg = args[i];
                if (arg == "--smoke") { options.Smoke = true; continue; }
                if ((arg == "--url" || arg == "--width" || arg == "--data-dir" || arg == "--test-message" || arg == "--eval") && i + 1 < args.Length)
                {
                    string value = args[++i];
                    if (arg == "--url") options.Url = value;
                    else if (arg == "--data-dir") options.DataDir = value;
                    else if (arg == "--test-message") options.TestMessage = value;
                    else if (arg == "--eval") options.EvalScript = value;
                    else int.TryParse(value, out options.Width);
                    continue;
                }
                if (arg.StartsWith("--url=")) options.Url = arg.Substring(6);
                else if (arg.StartsWith("--test-message=")) options.TestMessage = arg.Substring(15);
                else if (arg.StartsWith("--eval=")) options.EvalScript = arg.Substring(7);
                else if (arg.StartsWith("--data-dir=")) options.DataDir = arg.Substring(11);
                else if (arg.StartsWith("--width=")) int.TryParse(arg.Substring(8), out options.Width);
            }
            options.Width = Math.Max(320, Math.Min(900, options.Width));
            return options;
        }
    }

    internal sealed class PanelForm : Form
    {
        private readonly Options _options;
        private readonly WebView2 _web;
        private readonly System.Windows.Forms.Timer _follow;
        private IntPtr _kompas;
        private RECT _lastKompasRect;
        private bool _attached;
        private bool _closing;
        private string _userDataFolder;

        private const string KompasWord = "\u041A\u041E\u041C\u041F\u0410\u0421";
        private const int WM_HOTKEY = 0x0312;
        private const int WM_NCLBUTTONDOWN = 0xA1;
        private const int HTCAPTION = 0x2;
        private const int GWL_EXSTYLE = -20;
        private const int GWLP_HWNDPARENT = -8;
        private const int WS_EX_TOOLWINDOW = 0x00000080;
        private const int WS_EX_APPWINDOW = 0x00040000;
        private const int SW_HIDE = 0;
        private const int SW_SHOWNOACTIVATE = 4;
        private const uint SWP_NOACTIVATE = 0x0010;
        private const uint SWP_SHOWWINDOW = 0x0040;
        private const uint MOD_ALT = 0x0001;
        private const uint MOD_CONTROL = 0x0002;
        private static readonly IntPtr HWND_TOP = IntPtr.Zero;

        public PanelForm(Options options)
        {
            _options = options;
            Text = "Kimi for KOMPAS";
            FormBorderStyle = FormBorderStyle.None;
            ShowInTaskbar = false;
            StartPosition = FormStartPosition.Manual;
            BackColor = Color.FromArgb(11, 13, 17);
            DoubleBuffered = true;
            AutoScaleMode = AutoScaleMode.Dpi;
            ClientSize = new Size(options.Width, 900);
            MinimumSize = new Size(320, 480);

            _web = new WebView2
            {
                Dock = DockStyle.Fill,
                DefaultBackgroundColor = Color.FromArgb(11, 13, 17),
            };
            Controls.Add(_web);

            _follow = new System.Windows.Forms.Timer { Interval = 250 };
            _follow.Tick += delegate { FollowKompas(); };
            _follow.Start();

            Load += async delegate { Log("load"); await InitializeWebView(); Log("load done"); };
            FormClosing += OnFormClosing;

            if (options.Smoke)
            {
                var smokeTimer = new System.Windows.Forms.Timer { Interval = 5000 };
                smokeTimer.Tick += delegate { smokeTimer.Stop(); Close(); };
                smokeTimer.Start();
            }
        }

        protected override bool ShowWithoutActivation
        {
            get { return true; }
        }

        protected override void OnHandleCreated(EventArgs e)
        {
            base.OnHandleCreated(e);
            RegisterHotKey(Handle, 1, MOD_CONTROL | MOD_ALT, (int)Keys.K);
            int ex = GetWindowLong(Handle, GWL_EXSTYLE);
            SetWindowLong(Handle, GWL_EXSTYLE, (ex | WS_EX_TOOLWINDOW) & ~WS_EX_APPWINDOW);
            PositionPanel();
        }

        private async System.Threading.Tasks.Task InitializeWebView()
        {
            try
            {
                string baseDir = AppDomain.CurrentDomain.BaseDirectory;
                string localData = Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData);
                var candidates = new List<string>();
                if (!string.IsNullOrEmpty(_options.DataDir))
                    candidates.Add(_options.DataDir);
                candidates.Add(Path.Combine(localData, "KompasChat", "WebView2"));
                candidates.Add(Path.Combine(Path.GetTempPath(), "KompasChatWebView2"));
                candidates.Add(Path.Combine(baseDir, "WebView2Data"));

                _userDataFolder = "";
                foreach (string candidate in candidates)
                {
                    try
                    {
                        Directory.CreateDirectory(candidate);
                        string probe = Path.Combine(candidate, "_.probe");
                        File.WriteAllText(probe, "x");
                        File.Delete(probe);
                        _userDataFolder = candidate;
                        break;
                    }
                    catch { }
                }
                if (string.IsNullOrEmpty(_userDataFolder))
                    throw new IOException("No writable WebView2 data folder");
                var environment = await CoreWebView2Environment.CreateAsync(null, _userDataFolder, null);
                await _web.EnsureCoreWebView2Async(environment);
                var core = _web.CoreWebView2;
                core.Settings.AreDefaultContextMenusEnabled = false;
                core.Settings.IsStatusBarEnabled = false;
                core.Settings.AreDevToolsEnabled = true;
                core.Settings.IsZoomControlEnabled = false;
                core.WebMessageReceived += OnWebMessage;
                core.NewWindowRequested += OnNewWindow;
                core.NavigationCompleted += OnNavigationCompleted;
                core.ProcessFailed += delegate { TryReload(); };
                _web.Source = new Uri(_options.Url);
                Log("webview ready " + _userDataFolder);
            }
            catch (Exception ex)
            {
                Log("webview init failed: " + ex);
                try
                {
                    _web.NavigateToString(
                        "<html><body style='background:#0b0d11;color:#e7eaf0;font-family:Segoe UI;padding:24px'>" +
                        "<h3>WebView2 failed to start</h3><p>Install Microsoft Edge WebView2 Runtime.</p><pre style='white-space:pre-wrap'>" +
                        System.Security.SecurityElement.Escape(ex.Message) + "</pre></body></html>");
                }
                catch
                {
                    var errorLabel = new Label
                    {
                        Dock = DockStyle.Fill,
                        TextAlign = ContentAlignment.MiddleLeft,
                        Padding = new Padding(16),
                        ForeColor = Color.FromArgb(231, 234, 240),
                        BackColor = Color.FromArgb(11, 13, 17),
                        Text = "WebView2 failed to start." + Environment.NewLine + ex.Message,
                    };
                    Controls.Add(errorLabel);
                    errorLabel.BringToFront();
                }
            }
        }

        private async void OnNavigationCompleted(object sender, CoreWebView2NavigationCompletedEventArgs e)
        {
            if (!e.IsSuccess)
            {
                Log("navigation failed: " + e.WebErrorStatus);
                var retry = new System.Windows.Forms.Timer { Interval = 1500 };
                retry.Tick += delegate
                {
                    retry.Stop();
                    retry.Dispose();
                    TryReload();
                };
                retry.Start();
                return;
            }
            try
            {
                string state = await _web.CoreWebView2.ExecuteScriptAsync(
                    "JSON.stringify({href:location.href,hasSettings:new URLSearchParams(location.search).has('settings'),overlay:(document.getElementById('settings')||{}).className||''})");
                Log("nav " + state);
                if (!string.IsNullOrEmpty(_options.TestMessage))
                {
                    await System.Threading.Tasks.Task.Delay(900);
                    await _web.CoreWebView2.ExecuteScriptAsync(
                        "send(" + JsonQuote(_options.TestMessage) + ");");
                    Log("test message sent");
                }
                if (!string.IsNullOrEmpty(_options.EvalScript))
                {
                    await System.Threading.Tasks.Task.Delay(900);
                    await _web.CoreWebView2.ExecuteScriptAsync(_options.EvalScript);
                    Log("eval sent");
                }
            }
            catch (Exception ex)
            {
                Log("nav eval failed: " + ex.Message);
            }
        }

        private void TryReload()
        {
            try
            {
                if (_web.CoreWebView2 != null)
                {
                    _web.CoreWebView2.Navigate(_options.Url);
                }
            }
            catch { }
        }

        private void OnNewWindow(object sender, CoreWebView2NewWindowRequestedEventArgs e)
        {
            e.Handled = true;
            try
            {
                Process.Start(new ProcessStartInfo(e.Uri) { UseShellExecute = true });
            }
            catch { }
        }

        private void OnWebMessage(object sender, CoreWebView2WebMessageReceivedEventArgs e)
        {
            string message = "";
            try { message = e.TryGetWebMessageAsString(); } catch { }
            if (string.IsNullOrEmpty(message)) return;
            if (message.Contains("\"drag\"")) StartDrag();
            else if (message.Contains("\"close\"")) Close();
            else if (message.Contains("\"hide\"")) Hide();
        }

        private void StartDrag()
        {
            ReleaseCapture();
            SendMessage(Handle, WM_NCLBUTTONDOWN, (IntPtr)HTCAPTION, IntPtr.Zero);
        }

        private void FollowKompas()
        {
            if (_closing) return;
            if (!IsWindow(_kompas))
            {
                _attached = false;
                _kompas = FindKompasWindow();
                if (_kompas != IntPtr.Zero) Attach();
            }
            if (_kompas == IntPtr.Zero)
            {
                if (_attached) Detach();
                PositionStandalone();
                return;
            }

            if (IsIconic(_kompas) || !IsWindowVisible(_kompas))
            {
                if (Visible) ShowWindow(Handle, SW_HIDE);
                return;
            }

            RECT rect;
            GetWindowRect(_kompas, out rect);
            bool moved = rect.Left != _lastKompasRect.Left || rect.Top != _lastKompasRect.Top
                || rect.Right != _lastKompasRect.Right || rect.Bottom != _lastKompasRect.Bottom;
            if (moved || !Visible)
            {
                _lastKompasRect = rect;
                PositionPanel();
                if (!Visible) ShowWindow(Handle, SW_SHOWNOACTIVATE);
            }
        }

        private void Attach()
        {
            SetWindowLongPtr(Handle, GWLP_HWNDPARENT, _kompas);
            _attached = true;
            _lastKompasRect = new RECT();
            PositionPanel();
        }

        private void Detach()
        {
            SetWindowLongPtr(Handle, GWLP_HWNDPARENT, IntPtr.Zero);
            _attached = false;
        }

        private void PositionPanel()
        {
            if (_kompas == IntPtr.Zero) { PositionStandalone(); return; }
            RECT client;
            if (!GetClientRect(_kompas, out client)) { PositionStandalone(); return; }
            POINT origin = new POINT { X = 0, Y = 0 };
            ClientToScreen(_kompas, ref origin);
            int width = _options.Width;
            int x = origin.X + Math.Max(0, client.Right - width);
            int y = origin.Y;
            int height = Math.Max(480, client.Bottom);
            SetWindowPos(Handle, HWND_TOP, x, y, width, height, SWP_NOACTIVATE | SWP_SHOWWINDOW);
        }

        private void PositionStandalone()
        {
            Screen screen = Screen.PrimaryScreen;
            Rectangle area = screen.WorkingArea;
            int width = _options.Width;
            SetWindowPos(
                Handle, HWND_TOP,
                area.Right - width, area.Top, width, area.Height,
                SWP_NOACTIVATE | SWP_SHOWWINDOW);
        }

        private static IntPtr FindKompasWindow()
        {
            IntPtr foreground = GetForegroundWindow();
            IntPtr best = IntPtr.Zero;
            long bestArea = 0;
            int ownPid = Process.GetCurrentProcess().Id;

            EnumWindows(delegate (IntPtr hwnd, IntPtr lParam)
            {
                if (!IsWindowVisible(hwnd)) return true;
                if (IsIconic(hwnd)) return true;
                int pid;
                GetWindowThreadProcessId(hwnd, out pid);
                if (pid == ownPid || pid == 0) return true;
                string name = "";
                try { name = Process.GetProcessById(pid).ProcessName; } catch { return true; }
                if (!name.Equals("kStudy", StringComparison.OrdinalIgnoreCase)
                    && !name.Equals("KOMPAS", StringComparison.OrdinalIgnoreCase)
                    && !name.StartsWith("KOMPAS", StringComparison.OrdinalIgnoreCase))
                    return true;
                var sb = new StringBuilder(512);
                GetWindowText(hwnd, sb, sb.Capacity);
                string title = sb.ToString();
                if (title.IndexOf(KompasWord, StringComparison.OrdinalIgnoreCase) < 0
                    && title.IndexOf("KOMPAS", StringComparison.OrdinalIgnoreCase) < 0)
                    return true;
                RECT rect;
                GetWindowRect(hwnd, out rect);
                long area = (long)(rect.Right - rect.Left) * (rect.Bottom - rect.Top);
                if (hwnd == foreground) { best = hwnd; bestArea = long.MaxValue; return false; }
                if (area > bestArea) { best = hwnd; bestArea = area; }
                return true;
            }, IntPtr.Zero);

            return best;
        }

        protected override void WndProc(ref Message m)
        {
            if (m.Msg == WM_HOTKEY && m.WParam == (IntPtr)1)
            {
                if (Visible) Hide();
                else
                {
                    PositionPanel();
                    ShowWindow(Handle, SW_SHOWNOACTIVATE);
                }
                return;
            }
            base.WndProc(ref m);
        }

        private void OnFormClosing(object sender, FormClosingEventArgs e)
        {
            _closing = true;
            Log("form closing");
            _follow.Stop();
            UnregisterHotKey(Handle, 1);
            try { _web.Dispose(); } catch { }
        }

        private static void Log(string message)
        {
            try
            {
                File.AppendAllText(
                    Path.Combine(AppDomain.CurrentDomain.BaseDirectory, "panel.log"),
                    DateTime.Now.ToString("s") + " " + message + Environment.NewLine);
            }
            catch { }
        }

        private static string JsonQuote(string value)
        {
            var builder = new StringBuilder("\"");
            foreach (char c in value)
            {
                switch (c)
                {
                    case '\\': builder.Append("\\\\"); break;
                    case '"': builder.Append("\\\""); break;
                    case '\n': builder.Append("\\n"); break;
                    case '\r': builder.Append("\\r"); break;
                    case '\t': builder.Append("\\t"); break;
                    default: builder.Append(c); break;
                }
            }
            builder.Append('"');
            return builder.ToString();
        }

        [StructLayout(LayoutKind.Sequential)]
        private struct RECT { public int Left, Top, Right, Bottom; }

        [StructLayout(LayoutKind.Sequential)]
        private struct POINT { public int X, Y; }

        private delegate bool EnumWindowsProc(IntPtr hwnd, IntPtr lParam);

        [DllImport("user32.dll")]
        private static extern bool EnumWindows(EnumWindowsProc callback, IntPtr lParam);
        [DllImport("user32.dll")]
        private static extern bool IsWindow(IntPtr hwnd);
        [DllImport("user32.dll")]
        private static extern bool ShowWindow(IntPtr hwnd, int cmd);
        [DllImport("user32.dll")]
        private static extern bool IsWindowVisible(IntPtr hwnd);
        [DllImport("user32.dll")]
        private static extern bool IsIconic(IntPtr hwnd);
        [DllImport("user32.dll")]
        private static extern bool GetWindowRect(IntPtr hwnd, out RECT rect);
        [DllImport("user32.dll")]
        private static extern bool GetClientRect(IntPtr hwnd, out RECT rect);
        [DllImport("user32.dll")]
        private static extern bool ClientToScreen(IntPtr hwnd, ref POINT point);
        [DllImport("user32.dll")]
        private static extern bool SetWindowPos(
            IntPtr hwnd, IntPtr after, int x, int y, int cx, int cy, uint flags);
        [DllImport("user32.dll")]
        private static extern int GetWindowLong(IntPtr hwnd, int index);
        [DllImport("user32.dll")]
        private static extern int SetWindowLong(IntPtr hwnd, int index, int value);
        [DllImport("user32.dll", EntryPoint = "GetWindowLongPtr")]
        private static extern IntPtr GetWindowLongPtr(IntPtr hwnd, int index);
        [DllImport("user32.dll", EntryPoint = "SetWindowLongPtr")]
        private static extern IntPtr SetWindowLongPtr(IntPtr hwnd, int index, IntPtr value);
        [DllImport("user32.dll")]
        private static extern IntPtr GetForegroundWindow();
        [DllImport("user32.dll")]
        private static extern int GetWindowThreadProcessId(IntPtr hwnd, out int pid);
        [DllImport("user32.dll", CharSet = CharSet.Unicode)]
        private static extern int GetWindowText(IntPtr hwnd, StringBuilder text, int max);
        [DllImport("user32.dll")]
        private static extern bool ReleaseCapture();
        [DllImport("user32.dll")]
        private static extern IntPtr SendMessage(IntPtr hwnd, int msg, IntPtr wParam, IntPtr lParam);
        [DllImport("user32.dll")]
        private static extern bool RegisterHotKey(IntPtr hwnd, int id, uint modifiers, int vk);
        [DllImport("user32.dll")]
        private static extern bool UnregisterHotKey(IntPtr hwnd, int id);
    }
}







