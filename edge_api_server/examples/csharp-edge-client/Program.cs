using System.Diagnostics;
using System.Globalization;
using System.Net;
using System.Net.Http.Headers;
using System.Net.Http.Json;
using System.Security.Cryptography;
using System.Text;
using System.Text.Json;

namespace Rist.EdgeClient;

internal static class Program
{
    private static async Task<int> Main(string[] args)
    {
        Console.OutputEncoding = Encoding.UTF8;
        if (args.Length == 0 || args.Contains("--help", StringComparer.OrdinalIgnoreCase))
        {
            CliOptions.PrintUsage();
            return 0;
        }

        using var cancellation = new CancellationTokenSource();
        Console.CancelKeyPress += (_, eventArgs) =>
        {
            eventArgs.Cancel = true;
            cancellation.Cancel();
        };

        try
        {
            var options = CliOptions.Parse(args);
            if (options.Command == "manifest")
            {
                var manifest = await LocalBundle.BuildAsync(
                    options.InputPath!,
                    cancellation.Token);
                Console.WriteLine(JsonSerializer.Serialize(
                    manifest.Select(item => new
                    {
                        item.RelativePath,
                        item.SizeBytes,
                        item.Sha256,
                        item.LastModifiedAt,
                    }),
                    EdgeApiClient.JsonOptions));
                return 0;
            }

            using var api = new EdgeApiClient(options);
            if (options.Command == "requests")
            {
                await PrintRequestsAsync(api, options, cancellation.Token);
                return 0;
            }

            await SubmitAsync(api, options, cancellation.Token);
            return 0;
        }
        catch (OperationCanceledException)
        {
            Console.Error.WriteLine("작업이 취소되었습니다.");
            return 130;
        }
        catch (EdgeApiException exception)
        {
            Console.Error.WriteLine(
                $"Edge API 오류: HTTP {(int)exception.StatusCode} " +
                $"{exception.Code} - {exception.Message}");
            return 2;
        }
        catch (Exception exception)
        {
            Console.Error.WriteLine($"실행 오류: {exception.Message}");
            return 1;
        }
    }

    private static async Task PrintRequestsAsync(
        EdgeApiClient api,
        CliOptions options,
        CancellationToken cancellationToken)
    {
        var response = await api.GetRequestsAsync(
            options.AnalysisType!,
            options.PageSize,
            options.IncludeCompleted,
            cancellationToken);

        Console.WriteLine(
            $"{response.ExperimentType ?? options.AnalysisType} 의뢰 {response.Items.Count}건");
        foreach (var item in response.Items)
        {
            Console.WriteLine(
                $"{item.RequestNumber,-16} {item.ExperimentCode,-10} " +
                $"{item.SampleName,-22} {item.TestMethodName} " +
                $"[{item.RequestStateName ?? "-"}]");
        }
    }

    private static async Task SubmitAsync(
        EdgeApiClient api,
        CliOptions options,
        CancellationToken cancellationToken)
    {
        var bundle = await LocalBundle.BuildAsync(
            options.InputPath!,
            cancellationToken);
        Console.WriteLine(
            $"Bundle: {bundle.Count}개 파일, " +
            $"{bundle.Sum(item => item.SizeBytes):N0} bytes");

        var job = await api.CreateJobAsync(options, bundle, cancellationToken);
        Console.WriteLine($"작업 등록: {job.JobId} ({job.Status})");
        var current = await api.GetStatusAsync(job.JobId, cancellationToken);
        var currentStatus = current.Status.ToUpperInvariant();

        if (currentStatus is "CREATED" or "UPLOADING")
        {
            var existing = await api.GetFilesAsync(job.JobId, cancellationToken);
            var existingByPath = existing.Files.ToDictionary(
                item => item.RelativePath,
                StringComparer.Ordinal);
            for (var index = 0; index < bundle.Count; index++)
            {
                var item = bundle[index];
                UploadedFileResponse uploaded;
                if (existingByPath.TryGetValue(item.RelativePath, out var serverFile)
                    && serverFile.SizeBytes == item.SizeBytes
                    && string.Equals(serverFile.Sha256, item.Sha256, StringComparison.Ordinal))
                {
                    Console.WriteLine(
                        $"파일 확인 [{index + 1}/{bundle.Count}]: " +
                        $"{item.RelativePath} (이미 업로드됨)");
                    continue;
                }
                if (serverFile is not null)
                {
                    uploaded = await api.ReplaceAsync(job.JobId, item, cancellationToken);
                }
                else
                {
                    uploaded = await api.UploadAsync(job.JobId, item, cancellationToken);
                }
                Console.WriteLine(
                    $"파일 업로드 [{index + 1}/{bundle.Count}]: " +
                    $"{uploaded.RelativePath} ({uploaded.SizeBytes:N0} bytes)");
            }

            var serverFiles = await api.GetFilesAsync(job.JobId, cancellationToken);
            LocalBundle.VerifyServerList(bundle, serverFiles.Files);
            Console.WriteLine("서버 파일 목록과 로컬 SHA-256 일치 확인");

            var verified = await api.CompleteUploadAsync(
                job.JobId,
                serverFiles.Files,
                cancellationToken);
            Console.WriteLine(
                $"업로드 확정: {verified.Status}, {verified.VerifiedFileCount}개");
            currentStatus = verified.Status.ToUpperInvariant();
        }
        else
        {
            var serverFiles = await api.GetFilesAsync(job.JobId, cancellationToken);
            LocalBundle.VerifyServerList(bundle, serverFiles.Files);
            Console.WriteLine(
                $"기존 작업 재개: {currentStatus}, 서버 bundle 무결성 일치");
        }

        if (currentStatus == "FILES_VERIFIED")
        {
            var requested = await api.RequestReportAsync(
                job.JobId,
                options.AnalysisType!,
                options.IncludeRawFiles,
                cancellationToken);
            Console.WriteLine($"보고서 생성 요청: {requested.Status}");
            currentStatus = requested.Status.ToUpperInvariant();
        }
        if (currentStatus is not ("QUEUED" or "PROCESSING" or "COMPLETED"))
        {
            throw new InvalidOperationException(
                $"현재 작업 상태에서는 보고서를 진행할 수 없습니다: {currentStatus}");
        }

        var result = currentStatus == "COMPLETED"
            ? await api.GetStatusAsync(job.JobId, cancellationToken)
            : await api.WaitForCompletionAsync(
                job.JobId,
                TimeSpan.FromSeconds(options.PollTimeoutSeconds),
                TimeSpan.FromSeconds(options.PollIntervalSeconds),
                cancellationToken);

        if (string.Equals(result.Status, "FAILED", StringComparison.OrdinalIgnoreCase))
        {
            throw new InvalidOperationException(
                $"보고서 생성 실패: {result.Error?.Code} - {result.Error?.Message}");
        }
        if (!string.Equals(result.Status, "COMPLETED", StringComparison.OrdinalIgnoreCase))
        {
            throw new TimeoutException(
                $"보고서 생성 대기시간을 초과했습니다. 현재 상태: {result.Status}");
        }
        if (string.IsNullOrWhiteSpace(result.ReviewUrl))
        {
            throw new InvalidOperationException(
                "COMPLETED 응답에 reviewUrl이 없습니다.");
        }

        var reviewUrl = api.ResolveUrl(result.ReviewUrl);
        Console.WriteLine($"보고서 생성 완료: {result.ReportId}");
        Console.WriteLine($"전송 상태: {result.ReportStatus ?? "READY_FOR_REVIEW"}");
        Console.WriteLine($"검토 주소: {reviewUrl}");
        Console.WriteLine(
            "SSO 인증과 LIMS 전송 승인은 이 주소를 로그인된 브라우저에서 수행합니다.");

        if (options.OpenReview)
        {
            Process.Start(new ProcessStartInfo
            {
                FileName = reviewUrl,
                UseShellExecute = true,
            });
        }
    }
}

internal sealed record CliOptions(
    string Command,
    string BaseUrl,
    string? AnalysisType,
    string? InputPath,
    string? RequestNumber,
    string? ExperimentCode,
    string? EquipmentCode,
    string? OperatorId,
    string HostName,
    string ClientVersion,
    bool IncludeRawFiles,
    bool IncludeCompleted,
    bool OpenReview,
    int PageSize,
    double PollIntervalSeconds,
    double PollTimeoutSeconds,
    double RequestTimeoutSeconds)
{
    private static readonly HashSet<string> BooleanFlags = new(StringComparer.OrdinalIgnoreCase)
    {
        "--include-raw",
        "--include-completed",
        "--open-review",
    };

    public static CliOptions Parse(string[] args)
    {
        var command = args[0].Trim().ToLowerInvariant();
        if (command is not ("requests" or "manifest" or "submit"))
        {
            throw new ArgumentException(
                "첫 번째 인수는 requests, manifest 또는 submit이어야 합니다.");
        }

        var values = new Dictionary<string, string>(StringComparer.OrdinalIgnoreCase);
        for (var index = 1; index < args.Length; index++)
        {
            var key = args[index];
            if (!key.StartsWith("--", StringComparison.Ordinal))
            {
                throw new ArgumentException($"알 수 없는 인수입니다: {key}");
            }
            if (BooleanFlags.Contains(key))
            {
                values[key] = "true";
                continue;
            }
            if (index + 1 >= args.Length || args[index + 1].StartsWith("--", StringComparison.Ordinal))
            {
                throw new ArgumentException($"{key} 값을 입력해야 합니다.");
            }
            values[key] = args[++index];
        }

        string? Value(string key) => values.GetValueOrDefault(key)?.Trim();
        string Required(string key)
        {
            var value = Value(key);
            return !string.IsNullOrWhiteSpace(value)
                ? value
                : throw new ArgumentException($"{key}는 필수입니다.");
        }
        int PositiveInt(string key, int fallback)
        {
            var text = Value(key);
            if (string.IsNullOrWhiteSpace(text))
            {
                return fallback;
            }
            if (!int.TryParse(text, NumberStyles.None, CultureInfo.InvariantCulture, out var number) || number <= 0)
            {
                throw new ArgumentException($"{key}는 0보다 큰 정수여야 합니다.");
            }
            return number;
        }
        double PositiveDouble(string key, double fallback)
        {
            var text = Value(key);
            if (string.IsNullOrWhiteSpace(text))
            {
                return fallback;
            }
            if (!double.TryParse(text, NumberStyles.Float, CultureInfo.InvariantCulture, out var number) || number <= 0)
            {
                throw new ArgumentException($"{key}는 0보다 큰 숫자여야 합니다.");
            }
            return number;
        }

        var baseUrl = Value("--base-url")
            ?? Environment.GetEnvironmentVariable("RIST_EDGE_BASE_URL")
            ?? "http://127.0.0.1:8000";
        if (!Uri.TryCreate(baseUrl.TrimEnd('/') + "/", UriKind.Absolute, out var parsedBase)
            || parsedBase.Scheme is not ("http" or "https"))
        {
            throw new ArgumentException("--base-url은 유효한 HTTP(S) 절대 주소여야 합니다.");
        }

        string? analysisType = null;
        string? inputPath = null;
        string? requestNumber = null;
        string? experimentCode = null;
        string? equipmentCode = null;
        string? operatorId = null;

        if (command is "requests" or "submit")
        {
            analysisType = Required("--analysis-type").ToUpperInvariant();
            if (analysisType is not ("XRD" or "TEM"))
            {
                throw new ArgumentException("--analysis-type은 XRD 또는 TEM이어야 합니다.");
            }
        }
        if (command is "manifest" or "submit")
        {
            inputPath = Path.GetFullPath(Required("--input"));
        }
        if (command == "submit")
        {
            requestNumber = Required("--request-number");
            experimentCode = Required("--experiment-code");
            equipmentCode = Required("--equipment-code");
            operatorId = Required("--operator-id");
        }

        return new CliOptions(
            command,
            parsedBase.ToString(),
            analysisType,
            inputPath,
            requestNumber,
            experimentCode,
            equipmentCode,
            operatorId,
            Value("--host-name") ?? Environment.MachineName,
            Value("--client-version") ?? "rist-edge-reference-1.0.0",
            values.ContainsKey("--include-raw"),
            values.ContainsKey("--include-completed"),
            values.ContainsKey("--open-review"),
            Math.Min(PositiveInt("--page-size", 50), 200),
            PositiveDouble("--poll-interval", 3),
            PositiveDouble("--poll-timeout", 900),
            PositiveDouble("--request-timeout", 300));
    }

    public static void PrintUsage()
    {
        Console.WriteLine("""
RIST Edge XRD/TEM C# 참조 클라이언트

의뢰 조회:
  dotnet run --project Rist.EdgeClient.csproj -- requests \
    --base-url https://edge.example \
    --analysis-type XRD

Bundle 무결성 목록 생성:
  dotnet run --project Rist.EdgeClient.csproj -- manifest \
    --input /path/to/xrd-or-tem-bundle

업로드부터 보고서 생성·검토 URL 확인:
  dotnet run --project Rist.EdgeClient.csproj -- submit \
    --base-url https://edge.example \
    --analysis-type XRD \
    --input /path/to/xrd-bundle \
    --request-number 2026M00001 \
    --experiment-code A23141 \
    --equipment-code XRD-PC-01 \
    --operator-id employee01 \
    --include-raw \
    --open-review

submit은 보고서 생성까지만 기계 API로 처리합니다. reviewUrl의 SSO 인증과
LIMS 전송 승인은 브라우저에서 사용자가 직접 수행해야 합니다.
""");
    }
}

internal static class LocalBundle
{
    private static readonly HashSet<string> IgnoredNames = new(StringComparer.OrdinalIgnoreCase)
    {
        ".DS_Store",
        "Thumbs.db",
    };

    public static async Task<IReadOnlyList<LocalBundleFile>> BuildAsync(
        string inputPath,
        CancellationToken cancellationToken)
    {
        IEnumerable<(string FullPath, string RelativePath)> paths;
        if (File.Exists(inputPath))
        {
            paths = new[] { (inputPath, Path.GetFileName(inputPath)) };
        }
        else if (Directory.Exists(inputPath))
        {
            paths = Directory.EnumerateFiles(inputPath, "*", SearchOption.AllDirectories)
                .Where(path => !IgnoredNames.Contains(Path.GetFileName(path)))
                .Select(path => (
                    path,
                    Path.GetRelativePath(inputPath, path).Replace('\\', '/')));
        }
        else
        {
            throw new FileNotFoundException("입력 파일 또는 폴더를 찾을 수 없습니다.", inputPath);
        }

        var result = new List<LocalBundleFile>();
        foreach (var (fullPath, relativePath) in paths.OrderBy(item => item.RelativePath, StringComparer.Ordinal))
        {
            cancellationToken.ThrowIfCancellationRequested();
            if (relativePath.StartsWith("../", StringComparison.Ordinal)
                || relativePath.Contains("/../", StringComparison.Ordinal)
                || Path.IsPathRooted(relativePath))
            {
                throw new InvalidOperationException($"안전하지 않은 상대 경로입니다: {relativePath}");
            }
            var info = new FileInfo(fullPath);
            await using var stream = info.OpenRead();
            using var sha = SHA256.Create();
            var digest = await sha.ComputeHashAsync(stream, cancellationToken);
            result.Add(new LocalBundleFile(
                fullPath,
                relativePath,
                info.Length,
                Convert.ToHexString(digest).ToLowerInvariant(),
                info.LastWriteTimeUtc.ToString("O", CultureInfo.InvariantCulture)));
        }
        if (result.Count == 0)
        {
            throw new InvalidOperationException("입력 bundle에 전송할 파일이 없습니다.");
        }
        return result;
    }

    public static void VerifyServerList(
        IReadOnlyList<LocalBundleFile> local,
        IReadOnlyList<UploadedFileResponse> server)
    {
        var localByPath = local.ToDictionary(item => item.RelativePath, StringComparer.Ordinal);
        var serverByPath = server.ToDictionary(item => item.RelativePath, StringComparer.Ordinal);
        var missing = localByPath.Keys.Except(serverByPath.Keys, StringComparer.Ordinal).ToArray();
        var unexpected = serverByPath.Keys.Except(localByPath.Keys, StringComparer.Ordinal).ToArray();
        if (missing.Length > 0 || unexpected.Length > 0)
        {
            throw new InvalidOperationException(
                $"서버 파일 목록 불일치. 누락=[{string.Join(", ", missing)}], " +
                $"추가=[{string.Join(", ", unexpected)}]");
        }
        foreach (var (path, localFile) in localByPath)
        {
            var serverFile = serverByPath[path];
            if (localFile.SizeBytes != serverFile.SizeBytes
                || !string.Equals(localFile.Sha256, serverFile.Sha256, StringComparison.Ordinal))
            {
                throw new InvalidOperationException(
                    $"서버 파일 무결성 불일치: {path}");
            }
        }
    }
}

internal sealed class EdgeApiClient : IDisposable
{
    public static readonly JsonSerializerOptions JsonOptions = new(JsonSerializerDefaults.Web)
    {
        WriteIndented = true,
    };

    private static readonly HttpStatusCode[] RetryableStatuses =
    {
        HttpStatusCode.RequestTimeout,
        HttpStatusCode.TooManyRequests,
        HttpStatusCode.BadGateway,
        HttpStatusCode.ServiceUnavailable,
        HttpStatusCode.GatewayTimeout,
    };

    private readonly HttpClient _http;
    private readonly CliOptions _options;

    public EdgeApiClient(CliOptions options)
    {
        _options = options;
        _http = new HttpClient
        {
            BaseAddress = new Uri(options.BaseUrl),
            Timeout = Timeout.InfiniteTimeSpan,
        };
        _http.DefaultRequestHeaders.TryAddWithoutValidation("X-Client-Type", "C#/.NET");
        _http.DefaultRequestHeaders.TryAddWithoutValidation("X-Client-Name", "RIST Edge reference client");
        _http.DefaultRequestHeaders.TryAddWithoutValidation("X-Client-Version", options.ClientVersion);
        _http.DefaultRequestHeaders.UserAgent.Add(new ProductInfoHeaderValue(
            "Rist-EdgeClient",
            "1.0.0"));
    }

    public void Dispose() => _http.Dispose();

    public string ResolveUrl(string path) => new Uri(_http.BaseAddress!, path).ToString();

    public Task<RequestListResponse> GetRequestsAsync(
        string analysisType,
        int pageSize,
        bool includeCompleted,
        CancellationToken cancellationToken)
    {
        var path = "/api/v1/requests?page=1"
            + $"&pageSize={pageSize}"
            + $"&experimentType={Uri.EscapeDataString(analysisType)}"
            + $"&includeCompleted={includeCompleted.ToString().ToLowerInvariant()}";
        return SendAsync<RequestListResponse>(
            () => CreateRequest(HttpMethod.Get, path),
            cancellationToken);
    }

    public Task<CreateJobResponse> CreateJobAsync(
        CliOptions options,
        IReadOnlyList<LocalBundleFile> bundle,
        CancellationToken cancellationToken)
    {
        var payload = new CreateJobRequest(
            new JobPk(
                options.RequestNumber!,
                options.ExperimentCode!,
                options.EquipmentCode!,
                options.OperatorId!),
            new SourcePc(options.HostName, null, options.ClientVersion),
            new BundleDeclaration(bundle.Count, bundle.Sum(item => item.SizeBytes)));
        return SendAsync<CreateJobResponse>(
            () => CreateJsonRequest(
                HttpMethod.Post,
                "/api/v1/jobs",
                payload,
                IdempotencyKey(
                    $"create:{options.RequestNumber}:{options.ExperimentCode}:" +
                    $"{options.EquipmentCode}:{options.OperatorId}")),
            cancellationToken);
    }

    public Task<UploadedFileResponse> UploadAsync(
        string jobId,
        LocalBundleFile file,
        CancellationToken cancellationToken)
    {
        return SendAsync<UploadedFileResponse>(
            () =>
            {
                var request = CreateRequest(
                    HttpMethod.Post,
                    $"/api/v1/jobs/{Uri.EscapeDataString(jobId)}/files",
                    IdempotencyKey($"upload:{jobId}:{file.RelativePath}:{file.Sha256}"));
                var form = new MultipartFormDataContent();
                var streamContent = new StreamContent(File.OpenRead(file.FullPath));
                streamContent.Headers.ContentType = new MediaTypeHeaderValue("application/octet-stream");
                form.Add(streamContent, "file", Path.GetFileName(file.FullPath));
                form.Add(new StringContent(file.RelativePath, Encoding.UTF8), "relativePath");
                form.Add(new StringContent(file.SizeBytes.ToString(CultureInfo.InvariantCulture)), "sizeBytes");
                form.Add(new StringContent(file.Sha256, Encoding.ASCII), "sha256");
                form.Add(new StringContent(file.LastModifiedAt, Encoding.ASCII), "lastModifiedAt");
                request.Content = form;
                return request;
            },
            cancellationToken);
    }

    public Task<UploadedFileResponse> ReplaceAsync(
        string jobId,
        LocalBundleFile file,
        CancellationToken cancellationToken)
    {
        return SendAsync<UploadedFileResponse>(
            () =>
            {
                var request = CreateRequest(
                    HttpMethod.Put,
                    $"/api/v1/jobs/{Uri.EscapeDataString(jobId)}/files/{EncodePath(file.RelativePath)}",
                    IdempotencyKey($"replace:{jobId}:{file.RelativePath}:{file.Sha256}"));
                var form = new MultipartFormDataContent();
                var streamContent = new StreamContent(File.OpenRead(file.FullPath));
                streamContent.Headers.ContentType = new MediaTypeHeaderValue("application/octet-stream");
                form.Add(streamContent, "file", Path.GetFileName(file.FullPath));
                form.Add(new StringContent(file.SizeBytes.ToString(CultureInfo.InvariantCulture)), "sizeBytes");
                form.Add(new StringContent(file.Sha256, Encoding.ASCII), "sha256");
                form.Add(new StringContent(file.LastModifiedAt, Encoding.ASCII), "lastModifiedAt");
                request.Content = form;
                return request;
            },
            cancellationToken);
    }

    public Task<FileListResponse> GetFilesAsync(
        string jobId,
        CancellationToken cancellationToken) =>
        SendAsync<FileListResponse>(
            () => CreateRequest(
                HttpMethod.Get,
                $"/api/v1/jobs/{Uri.EscapeDataString(jobId)}/files"),
            cancellationToken);

    public Task<JobStatusResponse> GetStatusAsync(
        string jobId,
        CancellationToken cancellationToken) =>
        SendAsync<JobStatusResponse>(
            () => CreateRequest(
                HttpMethod.Get,
                $"/api/v1/jobs/{Uri.EscapeDataString(jobId)}"),
            cancellationToken);

    public Task<CompleteUploadResponse> CompleteUploadAsync(
        string jobId,
        IReadOnlyList<UploadedFileResponse> files,
        CancellationToken cancellationToken)
    {
        var payload = new CompleteUploadRequest(
            files.Count,
            files.Sum(item => item.SizeBytes),
            files.Select(item => new BundleFile(
                item.RelativePath,
                item.SizeBytes,
                item.Sha256)).ToArray());
        return SendAsync<CompleteUploadResponse>(
            () => CreateJsonRequest(
                HttpMethod.Post,
                $"/api/v1/jobs/{Uri.EscapeDataString(jobId)}/uploads/complete",
                payload,
                IdempotencyKey($"complete:{jobId}")),
            cancellationToken);
    }

    public Task<GenerateReportResponse> RequestReportAsync(
        string jobId,
        string analysisType,
        bool includeRawFiles,
        CancellationToken cancellationToken)
    {
        var format = string.Equals(analysisType, "XRD", StringComparison.OrdinalIgnoreCase)
            ? "HTML"
            : "PPTX";
        var payload = new GenerateReportRequest(
            DateTimeOffset.UtcNow.ToString("O", CultureInfo.InvariantCulture),
            new ReportOptions(new[] { format }, includeRawFiles));
        return SendAsync<GenerateReportResponse>(
            () => CreateJsonRequest(
                HttpMethod.Post,
                $"/api/v1/jobs/{Uri.EscapeDataString(jobId)}/report",
                payload,
                IdempotencyKey($"report:{jobId}:{format}:{includeRawFiles}")),
            cancellationToken);
    }

    public async Task<JobStatusResponse> WaitForCompletionAsync(
        string jobId,
        TimeSpan timeout,
        TimeSpan interval,
        CancellationToken cancellationToken)
    {
        var deadline = DateTimeOffset.UtcNow + timeout;
        string? lastStatus = null;
        JobStatusResponse? latest = null;
        while (DateTimeOffset.UtcNow < deadline)
        {
            latest = await SendAsync<JobStatusResponse>(
                () => CreateRequest(
                    HttpMethod.Get,
                    $"/api/v1/jobs/{Uri.EscapeDataString(jobId)}"),
                cancellationToken);
            if (!string.Equals(lastStatus, latest.Status, StringComparison.Ordinal))
            {
                Console.WriteLine($"처리 상태: {latest.Status} ({latest.Progress}%)");
                lastStatus = latest.Status;
            }
            if (latest.Status is "COMPLETED" or "FAILED" or "UPLOAD_EXPIRED")
            {
                return latest;
            }
            await Task.Delay(interval, cancellationToken);
        }
        return latest ?? throw new TimeoutException("작업 상태를 조회하지 못했습니다.");
    }

    private HttpRequestMessage CreateJsonRequest<T>(
        HttpMethod method,
        string path,
        T payload,
        string? idempotencyKey = null)
    {
        var request = CreateRequest(method, path, idempotencyKey);
        request.Content = JsonContent.Create(payload, options: JsonOptions);
        return request;
    }

    private static string IdempotencyKey(string source)
    {
        var digest = SHA256.HashData(Encoding.UTF8.GetBytes(source));
        return "csharp-" + Convert.ToHexString(digest).ToLowerInvariant();
    }

    private static string EncodePath(string relativePath) =>
        string.Join(
            "/",
            relativePath.Split('/').Select(Uri.EscapeDataString));

    private static HttpRequestMessage CreateRequest(
        HttpMethod method,
        string path,
        string? idempotencyKey = null)
    {
        var request = new HttpRequestMessage(method, path);
        request.Headers.TryAddWithoutValidation("X-Request-Id", Guid.NewGuid().ToString());
        if (!string.IsNullOrWhiteSpace(idempotencyKey))
        {
            request.Headers.TryAddWithoutValidation("Idempotency-Key", idempotencyKey);
        }
        return request;
    }

    private async Task<T> SendAsync<T>(
        Func<HttpRequestMessage> requestFactory,
        CancellationToken cancellationToken)
    {
        const int maximumAttempts = 5;
        for (var attempt = 1; attempt <= maximumAttempts; attempt++)
        {
            using var requestTimeout = CancellationTokenSource.CreateLinkedTokenSource(cancellationToken);
            requestTimeout.CancelAfter(TimeSpan.FromSeconds(_options.RequestTimeoutSeconds));
            try
            {
                using var request = requestFactory();
                using var response = await _http.SendAsync(
                    request,
                    HttpCompletionOption.ResponseHeadersRead,
                    requestTimeout.Token);
                if (RetryableStatuses.Contains(response.StatusCode) && attempt < maximumAttempts)
                {
                    await DelayBeforeRetryAsync(attempt, cancellationToken);
                    continue;
                }
                var text = await response.Content.ReadAsStringAsync(requestTimeout.Token);
                if (!response.IsSuccessStatusCode)
                {
                    throw EdgeApiException.FromResponse(response.StatusCode, text);
                }
                return JsonSerializer.Deserialize<T>(text, JsonOptions)
                    ?? throw new InvalidOperationException("Edge API JSON 응답이 비어 있습니다.");
            }
            catch (OperationCanceledException) when (
                !cancellationToken.IsCancellationRequested && attempt < maximumAttempts)
            {
                await DelayBeforeRetryAsync(attempt, cancellationToken);
            }
            catch (HttpRequestException) when (attempt < maximumAttempts)
            {
                await DelayBeforeRetryAsync(attempt, cancellationToken);
            }
        }
        throw new HttpRequestException("Edge API 재시도 횟수를 초과했습니다.");
    }

    private static Task DelayBeforeRetryAsync(int attempt, CancellationToken cancellationToken)
    {
        var seconds = Math.Min(16, 1 << (attempt - 1));
        return Task.Delay(TimeSpan.FromSeconds(seconds), cancellationToken);
    }
}

internal sealed class EdgeApiException : Exception
{
    public HttpStatusCode StatusCode { get; }
    public string Code { get; }

    private EdgeApiException(HttpStatusCode statusCode, string code, string message)
        : base(message)
    {
        StatusCode = statusCode;
        Code = code;
    }

    public static EdgeApiException FromResponse(HttpStatusCode statusCode, string body)
    {
        try
        {
            var parsed = JsonSerializer.Deserialize<ApiErrorResponse>(
                body,
                EdgeApiClient.JsonOptions);
            if (parsed is not null)
            {
                return new EdgeApiException(
                    statusCode,
                    parsed.Code ?? "EDGE_API_ERROR",
                    parsed.Message ?? body);
            }
        }
        catch (JsonException)
        {
            // Fall back to the bounded raw body below.
        }
        var safeBody = body.Length <= 500 ? body : body[..500] + "…";
        return new EdgeApiException(statusCode, "EDGE_API_ERROR", safeBody);
    }
}

internal sealed record LocalBundleFile(
    string FullPath,
    string RelativePath,
    long SizeBytes,
    string Sha256,
    string LastModifiedAt);

internal sealed record JobPk(
    string RequestNumber,
    string ExperimentCode,
    string EquipmentCode,
    string OperatorId);

internal sealed record SourcePc(
    string HostName,
    string? DeclaredIpAddress,
    string ClientVersion);

internal sealed record BundleDeclaration(int FileCount, long TotalSizeBytes);

internal sealed record CreateJobRequest(
    JobPk Pk,
    SourcePc SourcePc,
    BundleDeclaration Bundle);

internal sealed record CreateJobResponse(
    string JobId,
    string Status,
    string CreatedAt,
    string UploadExpiresAt,
    bool Reused);

internal sealed record UploadedFileResponse(
    string FileId,
    string RelativePath,
    long SizeBytes,
    string Sha256,
    string Status,
    string UploadedAt);

internal sealed record FileListResponse(
    string JobId,
    IReadOnlyList<UploadedFileResponse> Files);

internal sealed record BundleFile(
    string RelativePath,
    long SizeBytes,
    string Sha256);

internal sealed record CompleteUploadRequest(
    int FileCount,
    long TotalSizeBytes,
    IReadOnlyList<BundleFile> Files);

internal sealed record CompleteUploadResponse(
    string JobId,
    string Status,
    int VerifiedFileCount,
    string VerifiedAt);

internal sealed record ReportOptions(
    IReadOnlyList<string> ReportFormats,
    bool IncludeRawFiles);

internal sealed record GenerateReportRequest(
    string RequestedAt,
    ReportOptions Options);

internal sealed record GenerateReportResponse(
    string JobId,
    string Status,
    string AcceptedAt);

internal sealed record ApiErrorDetail(
    string Code,
    string Message,
    bool Retryable);

internal sealed record JobStatusResponse(
    string JobId,
    JobPk Pk,
    string Status,
    int Progress,
    string CreatedAt,
    string? ProcessingStartedAt,
    string? CompletedAt,
    ApiErrorDetail? Error,
    string? AnalysisType,
    string? ReportId,
    string? ReportStatus,
    string? ReviewUrl);

internal sealed record RequestListResponse(
    int Page,
    int PageSize,
    string? ExperimentType,
    IReadOnlyList<RequestSummary> Items);

internal sealed record RequestSummary(
    string? RequestNumber,
    string? RequestStateName,
    string SampleName,
    string TestMethodName,
    string? ExperimentCode);

internal sealed record ApiErrorResponse(
    string? Code,
    string? Message,
    bool Retryable);
