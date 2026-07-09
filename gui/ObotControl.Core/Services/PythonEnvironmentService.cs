using System.Diagnostics;
using System.Runtime.InteropServices;
using System.Security.Cryptography;

namespace ObotControl.Core.Services;

public enum PythonCandidateKind { ExistingVenv, SystemInterpreter, ManagedAutoInstall }

/// <summary>One interpreter/venv the user could launch the engine with.</summary>
public sealed record PythonCandidate
{
    public required PythonCandidateKind Kind { get; init; }
    public required string DisplayName { get; init; }
    public string? PythonExePath { get; init; }
    public string? Version { get; init; }
    public bool? ObotInstalled { get; init; }

    public override string ToString() => DisplayName;
}

/// <summary>One step of <see cref="PythonEnvironmentService.EnsureAsync"/>, for a progress bar/log.</summary>
public sealed record SetupProgress(string Stage, string Message, double? PercentComplete = null, bool IsError = false);

public sealed record PythonEnvironmentStatus
{
    public bool IsReady { get; init; }
    public string Message { get; init; } = "";
    public PythonCandidate? Active { get; init; }
}

/// <summary>
/// Guarantees a working Python 3.12 environment for the engine without anyone touching a
/// terminal. Two tiers, tried in order: (1) an existing venv or system Python 3.12 already
/// on this machine (<see cref="DiscoverCandidatesAsync"/>), which <see cref="EnsureAsync"/>
/// just installs the project's dependencies into; (2) a fully self-contained fallback that
/// downloads the official python.org installer into a GUI-private folder, silently installs
/// it (no admin prompt, doesn't touch PATH or any existing Python), and builds a venv from
/// that. Windows only — <see cref="IsSupported"/> guards every entry point.
/// </summary>
public sealed class PythonEnvironmentService
{
    public const string PythonVersion = "3.12.10";
    private const string InstallerFileName = "python-3.12.10-amd64.exe";
    private const string DownloadUrl = "https://www.python.org/ftp/python/3.12.10/python-3.12.10-amd64.exe";
    // Corruption check only (published on python.org/downloads/release/python-31210/);
    // authenticity comes from HTTPS to the official domain, same as get-pip.py/pyenv-win.
    private const string InstallerMd5 = "5eddb0b6f12c852725de071ae681dde4";
    private const string VenvDirName = "OhBots";

    private static readonly HttpClient Http = new();

    public bool IsSupported => RuntimeInformation.IsOSPlatform(OSPlatform.Windows);

    public static string ManagedRootDir => Path.Combine(
        Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), "ObotControl");
    public static string ManagedPythonDir => Path.Combine(ManagedRootDir, "Python", PythonVersion);
    public static string ManagedPythonExe => Path.Combine(ManagedPythonDir, "python.exe");

    // -- fast, local-only status check (drives startup gating) -------------------------

    /// <summary>File-existence + pyvenv.cfg parse only — no subprocess, no network. Prefers
    /// <paramref name="rememberedPythonPath"/> if it still looks valid, else the first
    /// 3.12.x venv found directly under <paramref name="repoRoot"/>.</summary>
    public PythonEnvironmentStatus CheckStatus(string? repoRoot, string? rememberedPythonPath)
    {
        if (!IsSupported)
        {
            return new PythonEnvironmentStatus
            {
                IsReady = false,
                Message = "Automatic Python setup is only available on Windows — see README.md for manual setup.",
            };
        }

        if (!string.IsNullOrWhiteSpace(rememberedPythonPath) && File.Exists(rememberedPythonPath))
        {
            var version = ReadVenvVersion(rememberedPythonPath);
            if (version is not null && version.StartsWith("3.12", StringComparison.Ordinal))
            {
                return Ready(rememberedPythonPath, version);
            }
        }

        if (!string.IsNullOrWhiteSpace(repoRoot) && Directory.Exists(repoRoot))
        {
            foreach (var (_, exe, version) in ScanVenvDirs(repoRoot))
            {
                if (version.StartsWith("3.12", StringComparison.Ordinal)) return Ready(exe, version);
            }
        }

        return new PythonEnvironmentStatus { IsReady = false, Message = "No Python 3.12 environment found yet." };
    }

    private static PythonEnvironmentStatus Ready(string exe, string version) => new()
    {
        IsReady = true,
        Message = $"Ready — Python {version}",
        Active = new PythonCandidate
        {
            Kind = PythonCandidateKind.ExistingVenv,
            DisplayName = VenvDisplayName(exe, version),
            PythonExePath = exe,
            Version = version,
            ObotInstalled = null,
        },
    };

    // -- discovery -----------------------------------------------------------------------

    /// <summary>Every venv under the repo root, any system Python 3.12 not already inside
    /// one of those venvs, and a final synthetic "set up automatically" entry.</summary>
    public async Task<List<PythonCandidate>> DiscoverCandidatesAsync(string repoRoot, CancellationToken ct = default)
    {
        var results = new List<PythonCandidate>();
        if (!IsSupported)
        {
            results.Add(AutoInstallCandidate());
            return results;
        }

        if (!string.IsNullOrWhiteSpace(repoRoot) && Directory.Exists(repoRoot))
        {
            foreach (var (dir, exe, version) in ScanVenvDirs(repoRoot))
            {
                ct.ThrowIfCancellationRequested();
                var installed = await ProbeObotInstalledAsync(exe, repoRoot, ct).ConfigureAwait(false);
                results.Add(new PythonCandidate
                {
                    Kind = PythonCandidateKind.ExistingVenv,
                    DisplayName = VenvDisplayName(exe, version) + (installed ? "" : " — project not installed yet"),
                    PythonExePath = exe,
                    Version = version,
                    ObotInstalled = installed,
                });
            }
        }

        var venvPaths = results.Where(c => c.PythonExePath is not null)
            .Select(c => Path.GetFullPath(c.PythonExePath!))
            .ToHashSet(StringComparer.OrdinalIgnoreCase);

        foreach (var sysPython in await DiscoverSystemInterpretersAsync(ct).ConfigureAwait(false))
        {
            if (venvPaths.Contains(Path.GetFullPath(sysPython))) continue;
            var version = await ProbeVersionAsync(sysPython, ct).ConfigureAwait(false);
            if (version is null || !version.StartsWith("3.12", StringComparison.Ordinal)) continue;
            results.Add(new PythonCandidate
            {
                Kind = PythonCandidateKind.SystemInterpreter,
                DisplayName = $"System Python {version} ({sysPython})",
                PythonExePath = sysPython,
                Version = version,
                ObotInstalled = false,
            });
        }

        results.Add(AutoInstallCandidate());
        return results;
    }

    private static PythonCandidate AutoInstallCandidate() => new()
    {
        Kind = PythonCandidateKind.ManagedAutoInstall,
        DisplayName = "Set up automatically (downloads Python 3.12 + project dependencies)",
    };

    private static string VenvDisplayName(string exe, string version)
    {
        var venvDir = Path.GetDirectoryName(Path.GetDirectoryName(exe));
        var name = venvDir is null ? exe : Path.GetFileName(venvDir);
        return $"{name} (existing venv, Python {version})";
    }

    private static IEnumerable<(string Dir, string Exe, string Version)> ScanVenvDirs(string repoRoot)
    {
        foreach (var dir in Directory.EnumerateDirectories(repoRoot))
        {
            var exe = Path.Combine(dir, "Scripts", "python.exe");
            var cfg = Path.Combine(dir, "pyvenv.cfg");
            if (!File.Exists(exe) || !File.Exists(cfg)) continue;
            var version = ReadVenvVersionFromCfg(cfg);
            if (version is null) continue;
            yield return (dir, exe, version);
        }
    }

    private async Task<List<string>> DiscoverSystemInterpretersAsync(CancellationToken ct)
    {
        var found = new List<string>();

        var (pyCode, pyOut) = await RunCaptureAsync("py", new[] { "-0p" }, null, ct).ConfigureAwait(false);
        if (pyCode == 0)
        {
            foreach (var line in pyOut.Split('\n'))
            {
                if (!line.Contains("3.12", StringComparison.Ordinal)) continue;
                var path = line.Trim().Split((char[]?)null, StringSplitOptions.RemoveEmptyEntries).LastOrDefault();
                if (path is not null && File.Exists(path)) found.Add(path);
            }
        }

        var (whereCode, whereOut) = await RunCaptureAsync("where", new[] { "python" }, null, ct).ConfigureAwait(false);
        if (whereCode == 0)
        {
            foreach (var line in whereOut.Split('\n'))
            {
                var path = line.Trim();
                if (path.Length > 0 && File.Exists(path)) found.Add(path);
            }
        }

        return found.Select(Path.GetFullPath).Distinct(StringComparer.OrdinalIgnoreCase).ToList();
    }

    // -- ensure pipeline -------------------------------------------------------------------

    /// <summary>Makes <paramref name="candidate"/> usable: installs the project into an
    /// existing venv, or first creates <c>OhBots</c> from a base interpreter (a system
    /// Python, or — for <see cref="PythonCandidateKind.ManagedAutoInstall"/> — a freshly
    /// downloaded private one) and installs into that.</summary>
    public async Task<PythonEnvironmentStatus> EnsureAsync(
        PythonCandidate candidate, string repoRoot, IProgress<SetupProgress> progress, CancellationToken ct)
    {
        if (!IsSupported)
        {
            return new PythonEnvironmentStatus { IsReady = false, Message = "Automatic Python setup is only available on Windows." };
        }

        return candidate.Kind switch
        {
            PythonCandidateKind.ExistingVenv =>
                await EnsureVenvDepsAsync(candidate.PythonExePath!, repoRoot, progress, ct).ConfigureAwait(false),
            PythonCandidateKind.SystemInterpreter =>
                await CreateVenvAndEnsureAsync(candidate.PythonExePath!, repoRoot, progress, ct).ConfigureAwait(false),
            PythonCandidateKind.ManagedAutoInstall =>
                await CreateVenvAndEnsureAsync(
                    await EnsureManagedPythonAsync(progress, ct).ConfigureAwait(false), repoRoot, progress, ct).ConfigureAwait(false),
            _ => throw new ArgumentOutOfRangeException(nameof(candidate)),
        };
    }

    private async Task<string> EnsureManagedPythonAsync(IProgress<SetupProgress> progress, CancellationToken ct)
    {
        if (File.Exists(ManagedPythonExe))
        {
            var version = await ProbeVersionAsync(ManagedPythonExe, ct).ConfigureAwait(false);
            if (version == PythonVersion)
            {
                progress.Report(new SetupProgress("python", $"managed Python {PythonVersion} already installed"));
                return ManagedPythonExe;
            }
        }

        var cacheDir = Path.Combine(ManagedRootDir, "cache");
        Directory.CreateDirectory(cacheDir);
        var installerPath = Path.Combine(cacheDir, InstallerFileName);

        if (!File.Exists(installerPath) || !VerifyMd5(installerPath, InstallerMd5))
        {
            progress.Report(new SetupProgress("download", $"downloading {InstallerFileName}…", 0));
            await DownloadFileAsync(DownloadUrl, installerPath, progress, ct).ConfigureAwait(false);
            if (!VerifyMd5(installerPath, InstallerMd5))
            {
                File.Delete(installerPath);
                throw new InvalidOperationException("downloaded Python installer failed checksum verification");
            }
        }
        else
        {
            progress.Report(new SetupProgress("download", "using cached installer", 100));
        }

        progress.Report(new SetupProgress("install", "installing Python 3.12 (a few seconds)…"));
        Directory.CreateDirectory(ManagedPythonDir);
        var logPath = Path.Combine(cacheDir, "python-install.log");
        var installArgs = new[]
        {
            "/quiet",
            "InstallAllUsers=0",
            "PrependPath=0",
            "Include_launcher=0",
            "Include_test=0",
            "Include_tcltk=1",
            "Include_pip=1",
            $"TargetDir={ManagedPythonDir}",
            "/log", logPath,
        };
        var code = await RunProcessAsync(installerPath, installArgs, null,
            line => progress.Report(new SetupProgress("install", line)), ct).ConfigureAwait(false);
        if (code != 0 || !File.Exists(ManagedPythonExe))
        {
            throw new InvalidOperationException($"Python installer exited with code {code}; see {logPath}");
        }

        progress.Report(new SetupProgress("install", $"Python {PythonVersion} installed", 100));
        return ManagedPythonExe;
    }

    private async Task<PythonEnvironmentStatus> CreateVenvAndEnsureAsync(
        string basePython, string repoRoot, IProgress<SetupProgress> progress, CancellationToken ct)
    {
        var venvDir = Path.Combine(repoRoot, VenvDirName);
        if (Directory.Exists(venvDir))
        {
            var cfg = Path.Combine(venvDir, "pyvenv.cfg");
            var version = File.Exists(cfg) ? ReadVenvVersionFromCfg(cfg) : null;
            if (version is null || !version.StartsWith("3.12", StringComparison.Ordinal))
            {
                progress.Report(new SetupProgress("venv", $"removing stale environment at {venvDir}"));
                Directory.Delete(venvDir, recursive: true);
            }
        }

        var venvPython = Path.Combine(venvDir, "Scripts", "python.exe");
        if (!File.Exists(venvPython))
        {
            progress.Report(new SetupProgress("venv", $"creating {VenvDirName} virtual environment…"));
            var code = await RunProcessAsync(basePython, new[] { "-m", "venv", venvDir }, repoRoot,
                line => progress.Report(new SetupProgress("venv", line)), ct).ConfigureAwait(false);
            if (code != 0) throw new InvalidOperationException($"python -m venv failed with exit code {code}");
        }

        return await EnsureVenvDepsAsync(venvPython, repoRoot, progress, ct).ConfigureAwait(false);
    }

    private async Task<PythonEnvironmentStatus> EnsureVenvDepsAsync(
        string venvPython, string repoRoot, IProgress<SetupProgress> progress, CancellationToken ct)
    {
        progress.Report(new SetupProgress("pip", "upgrading pip…"));
        await RunProcessAsync(venvPython, new[] { "-m", "pip", "install", "--upgrade", "pip" }, repoRoot,
            line => progress.Report(new SetupProgress("pip", line)), ct).ConfigureAwait(false);
        // best-effort: an outdated-but-working pip shouldn't block setup.

        progress.Report(new SetupProgress("pip", "installing the obot package (pip install -e .)…"));
        var code = await RunProcessAsync(venvPython, new[] { "-m", "pip", "install", "-e", "." }, repoRoot,
            line => progress.Report(new SetupProgress("pip", line)), ct).ConfigureAwait(false);
        if (code != 0) return Failed(venvPython, "pip install -e . failed — see the log");

        var reqPath = Path.Combine(repoRoot, "requirements", "windows.txt");
        progress.Report(new SetupProgress("pip", "installing dependencies (requirements/windows.txt)…"));
        code = await RunProcessAsync(venvPython, new[] { "-m", "pip", "install", "-r", reqPath }, repoRoot,
            line => progress.Report(new SetupProgress("pip", line)), ct).ConfigureAwait(false);
        if (code != 0) return Failed(venvPython, "pip install -r requirements/windows.txt failed — see the log");

        progress.Report(new SetupProgress("verify", "verifying installation…"));
        var (verifyCode, _) = await RunCaptureAsync(venvPython, new[] { "-c", "import obot" }, repoRoot, ct).ConfigureAwait(false);
        if (verifyCode != 0) return Failed(venvPython, "verification failed: 'import obot' did not succeed");

        var version = await ProbeVersionAsync(venvPython, ct).ConfigureAwait(false) ?? "unknown";
        progress.Report(new SetupProgress("done", $"ready — Python {version}", 100));
        return new PythonEnvironmentStatus
        {
            IsReady = true,
            Message = $"Ready — Python {version}",
            Active = new PythonCandidate
            {
                Kind = PythonCandidateKind.ExistingVenv,
                DisplayName = VenvDisplayName(venvPython, version),
                PythonExePath = venvPython,
                Version = version,
                ObotInstalled = true,
            },
        };
    }

    private static PythonEnvironmentStatus Failed(string venvPython, string message) => new()
    {
        IsReady = false,
        Message = message,
        Active = new PythonCandidate
        {
            Kind = PythonCandidateKind.ExistingVenv,
            DisplayName = venvPython,
            PythonExePath = venvPython,
        },
    };

    // -- process / version / download helpers -----------------------------------------------

    private static async Task<bool> ProbeObotInstalledAsync(string exe, string repoRoot, CancellationToken ct)
    {
        var (code, _) = await RunCaptureAsync(exe, new[] { "-c", "import obot" }, repoRoot, ct).ConfigureAwait(false);
        return code == 0;
    }

    private static async Task<string?> ProbeVersionAsync(string exe, CancellationToken ct)
    {
        var (code, output) = await RunCaptureAsync(exe, new[] { "--version" }, null, ct).ConfigureAwait(false);
        if (code != 0) return null;
        var parts = output.Trim().Split(' ', StringSplitOptions.RemoveEmptyEntries);
        return parts.Length >= 2 ? parts[1].Trim() : null;
    }

    /// <summary>Given a venv's <c>Scripts\python.exe</c>, read the sibling pyvenv.cfg's version.</summary>
    private static string? ReadVenvVersion(string pythonExePath)
    {
        var scriptsDir = Path.GetDirectoryName(pythonExePath);
        var venvDir = scriptsDir is null ? null : Path.GetDirectoryName(scriptsDir);
        var cfg = venvDir is null ? null : Path.Combine(venvDir, "pyvenv.cfg");
        return cfg is not null && File.Exists(cfg) ? ReadVenvVersionFromCfg(cfg) : null;
    }

    private static string? ReadVenvVersionFromCfg(string cfgPath)
    {
        foreach (var line in File.ReadLines(cfgPath))
        {
            var idx = line.IndexOf('=');
            if (idx < 0) continue;
            if (!line[..idx].Trim().Equals("version", StringComparison.OrdinalIgnoreCase)) continue;
            return line[(idx + 1)..].Trim();
        }
        return null;
    }

    private static bool VerifyMd5(string path, string expectedHex)
    {
        using var md5 = MD5.Create();
        using var stream = File.OpenRead(path);
        var hash = md5.ComputeHash(stream);
        return Convert.ToHexString(hash).Equals(expectedHex, StringComparison.OrdinalIgnoreCase);
    }

    private static async Task DownloadFileAsync(string url, string destPath, IProgress<SetupProgress> progress, CancellationToken ct)
    {
        var tmpPath = destPath + ".tmp";
        Directory.CreateDirectory(Path.GetDirectoryName(destPath)!);
        using var response = await Http.GetAsync(url, HttpCompletionOption.ResponseHeadersRead, ct).ConfigureAwait(false);
        response.EnsureSuccessStatusCode();
        var total = response.Content.Headers.ContentLength;

        await using (var httpStream = await response.Content.ReadAsStreamAsync(ct).ConfigureAwait(false))
        await using (var fileStream = File.Create(tmpPath))
        {
            var buffer = new byte[81920];
            long read = 0;
            int n;
            while ((n = await httpStream.ReadAsync(buffer, ct).ConfigureAwait(false)) > 0)
            {
                await fileStream.WriteAsync(buffer.AsMemory(0, n), ct).ConfigureAwait(false);
                read += n;
                double? pct = total is > 0 ? Math.Round(read * 100.0 / total.Value, 1) : null;
                progress.Report(new SetupProgress("download", $"downloading… {read / 1_000_000.0:0.0} MB", pct));
            }
        }
        File.Move(tmpPath, destPath, overwrite: true);
    }

    private static async Task<(int ExitCode, string Output)> RunCaptureAsync(
        string exe, IEnumerable<string> args, string? cwd, CancellationToken ct)
    {
        var lines = new System.Text.StringBuilder();
        try
        {
            var code = await RunProcessAsync(exe, args, cwd, line => { lock (lines) lines.AppendLine(line); }, ct).ConfigureAwait(false);
            return (code, lines.ToString());
        }
        catch (System.ComponentModel.Win32Exception)
        {
            return (-1, ""); // exe not found on PATH — treated as "not available", not an error
        }
    }

    /// <summary>Runs a process to completion, streaming stdout/stderr lines to
    /// <paramref name="onLine"/>. Cancellation kills the whole process tree.</summary>
    private static async Task<int> RunProcessAsync(
        string exe, IEnumerable<string> args, string? cwd, Action<string>? onLine, CancellationToken ct)
    {
        var psi = new ProcessStartInfo
        {
            FileName = exe,
            WorkingDirectory = cwd ?? "",
            RedirectStandardOutput = true,
            RedirectStandardError = true,
            UseShellExecute = false,
            CreateNoWindow = true,
        };
        foreach (var a in args) psi.ArgumentList.Add(a);

        using var process = new Process { StartInfo = psi, EnableRaisingEvents = true };
        process.OutputDataReceived += (_, e) => { if (e.Data is not null) onLine?.Invoke(e.Data); };
        process.ErrorDataReceived += (_, e) => { if (e.Data is not null) onLine?.Invoke(e.Data); };

        process.Start();
        process.BeginOutputReadLine();
        process.BeginErrorReadLine();

        await using var registration = ct.Register(() =>
        {
            try { if (!process.HasExited) process.Kill(entireProcessTree: true); } catch { /* already gone */ }
        });

        await process.WaitForExitAsync(ct).ConfigureAwait(false);
        return process.ExitCode;
    }
}
