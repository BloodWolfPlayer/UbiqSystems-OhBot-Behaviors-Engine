using ObotControl.Core.Services;
using Xunit;

namespace ObotControl.Core.Tests;

/// <summary>
/// Covers the deterministic, local-only pieces of Python environment provisioning 
/// venv discovery/version parsing and status gating  against temp-directory fixtures.
/// Nothing here downloads or runs the real installer (too slow/networked for the suite;
/// that pipeline is exercised manually per docs/gui-plan.md's verification steps).
/// </summary>
public sealed class PythonEnvironmentServiceTests : IDisposable
{
    private readonly string _repoRoot;
    private readonly PythonEnvironmentService _service = new();

    public PythonEnvironmentServiceTests()
    {
        _repoRoot = Path.Combine(Path.GetTempPath(), "obot-pyenv-test-" + Guid.NewGuid());
        Directory.CreateDirectory(_repoRoot);
    }

    public void Dispose()
    {
        try { Directory.Delete(_repoRoot, recursive: true); } catch { /* best effort */ }
    }

    private string CreateFakeVenv(string name, string version)
    {
        // Mirrors the platform's real venv layout (Scripts\python.exe vs bin/python), which is what ScanVenvDirs looks for.
        var dir = Path.Combine(_repoRoot, name);
        var exe = Path.Combine(dir, PythonEnvironmentService.VenvRelativePythonPath);
        Directory.CreateDirectory(Path.GetDirectoryName(exe)!);
        File.WriteAllText(exe, "not a real executable");
        File.WriteAllText(Path.Combine(dir, "pyvenv.cfg"), $"home = /fake\nversion = {version}\ninclude-system-site-packages = false\n");
        return exe;
    }

    [Fact]
    public void CheckStatus_NoRepoNoRemembered_ReportsNotReady()
    {
        var status = _service.CheckStatus(null, null);
        Assert.False(status.IsReady);
    }

    [Fact]
    public void CheckStatus_FindsMatchingVenvUnderRepoRoot()
    {
        var exe = CreateFakeVenv("OhBots", "3.12.10");

        var status = _service.CheckStatus(_repoRoot, null);

        Assert.True(status.IsReady);
        Assert.Equal(exe, status.Active?.PythonExePath);
        Assert.Equal("3.12.10", status.Active?.Version);
    }

    [Fact]
    public void CheckStatus_IgnoresWrongPythonVersion()
    {
        CreateFakeVenv("OldVenv", "3.11.5");

        var status = _service.CheckStatus(_repoRoot, null);

        Assert.False(status.IsReady);
    }

    [Fact]
    public void CheckStatus_PrefersRememberedPathWhenStillValid()
    {
        CreateFakeVenv("OhBots", "3.12.10");
        var remembered = CreateFakeVenv("Other", "3.12.4");

        var status = _service.CheckStatus(_repoRoot, remembered);

        Assert.True(status.IsReady);
        Assert.Equal(remembered, status.Active?.PythonExePath);
    }

    [Fact]
    public void CheckStatus_FallsBackWhenRememberedPathIsGone()
    {
        var exe = CreateFakeVenv("OhBots", "3.12.10");
        var missing = Path.Combine(_repoRoot, "Deleted", PythonEnvironmentService.VenvRelativePythonPath);

        var status = _service.CheckStatus(_repoRoot, missing);

        Assert.True(status.IsReady);
        Assert.Equal(exe, status.Active?.PythonExePath);
    }

    [Fact]
    public async Task DiscoverCandidatesAsync_ListsEveryVenvAndEndsWithAutoInstall()
    {
        var ohbots = CreateFakeVenv("OhBots", "3.12.10");
        var custom = CreateFakeVenv("my-custom-env", "3.11.0");

        var found = await _service.DiscoverCandidatesAsync(_repoRoot);

        Assert.Contains(found, c => c.Kind == PythonCandidateKind.ExistingVenv && c.PythonExePath == ohbots && c.Version == "3.12.10");
        Assert.Contains(found, c => c.Kind == PythonCandidateKind.ExistingVenv && c.PythonExePath == custom && c.Version == "3.11.0");
        Assert.Equal(PythonCandidateKind.ManagedAutoInstall, found[^1].Kind);
    }

    [Fact]
    public async Task DiscoverCandidatesAsync_EmptyRepo_OnlyOffersAutoInstall()
    {
        var found = await _service.DiscoverCandidatesAsync(_repoRoot);

        Assert.DoesNotContain(found, c => c.Kind == PythonCandidateKind.ExistingVenv);
        Assert.Single(found, c => c.Kind == PythonCandidateKind.ManagedAutoInstall);
    }

    [Fact]
    public void PythonCandidate_ToStringIsDisplayName()
    {
        var candidate = new PythonCandidate { Kind = PythonCandidateKind.ExistingVenv, DisplayName = "OhBots (existing venv, Python 3.12.10)" };
        Assert.Equal(candidate.DisplayName, candidate.ToString());
    }

    [Fact]
    public void ManagedPythonDir_IsScopedToThePinnedVersion()
    {
        Assert.EndsWith(Path.Combine("Python", PythonEnvironmentService.PythonVersion), PythonEnvironmentService.ManagedPythonDir);
        Assert.Equal("3.12.10", PythonEnvironmentService.PythonVersion);
    }

    [Fact]
    public void Service_IsSupportedOnDesktopPlatforms()
    {
        Assert.True(_service.IsSupported);
    }

    [Fact]
    public void RequirementsFileName_MatchesThePlatform()
    {
        var name = PythonEnvironmentService.RequirementsFileName;
        Assert.Contains(name, new[] { "windows.txt", "linux.txt", "pi.txt" });
        Assert.Equal(OperatingSystem.IsWindows(), name == "windows.txt");
    }
}
