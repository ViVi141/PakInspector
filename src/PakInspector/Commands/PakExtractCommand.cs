using PakInspector.Data;
using PakInspector.Parser;
using Spectre.Console;
using System.CommandLine;
using System.IO.Compression;

namespace PakInspector.Commands;

internal static class PakExtractCommand
{
    public static Command Command
    {
        get
        {
            var fileArg = new Argument<FileInfo>("file")
            {
                Description = "Path to file"
            };
            fileArg.AcceptExistingOnly();

            var outputArg = new Argument<string?>("outputDir")
            {
                Description = "Path to output directory",
                DefaultValueFactory = res => string.Empty
            };
            outputArg.AcceptLegalFilePathsOnly();

            var filesOption = new Option<string[]>("--file", "-f")
            {
                Description = "Files to extract. Use paths as specified in inspection results",
                AllowMultipleArgumentsPerToken = true
            };

            var copyRawOption = new Option<bool>("--raw", "-r")
            {
                Description = "Extract files without processing"
            };

            var cmd = new Command("extract", "Extract files from the PAC1 file.");
            cmd.Arguments.Add(fileArg);
            cmd.Arguments.Add(outputArg);
            cmd.Options.Add(filesOption);
            cmd.Options.Add(copyRawOption);

            cmd.SetAction(parseResult => Execute(
                parseResult.GetValue(fileArg),
                parseResult.GetValue(outputArg),
                parseResult.GetValue(filesOption),
                parseResult.GetValue(copyRawOption)));

            return cmd;
        }
    }

    public static int Execute(FileInfo fileInfo,
                              string? outputPath,
                              string[]? files,
                              bool copyRaw)
    {
        var pak = AnsiConsole.Status().Start("Parsing .pak...", ctx => Pak.FromFile(fileInfo.FullName));

        var fileName = Path.GetFileNameWithoutExtension(fileInfo.FullName);
        var outputDir = string.IsNullOrEmpty(outputPath) ? fileName : outputPath;

        if (pak.Chunks.First(c => c.TypeId == Pak.Chunk.ChunkType.File).Body is not Pak.FileChunk fileChunk)
        {
            throw new Exception("Failed to parse file chunk");
        }

        var pakFiles = AnsiConsole.Status()
            .Start("Parsing file tree...", ctx => PakUtils.GetFiles(fileChunk.Root, "").ToDictionary(f => f.Path));

        List<PakFileEntry> filesToExtract = files is not null && files.Length > 0
            ? [.. files
                .Where(f => pakFiles.ContainsKey(f))
                .Select(f => pakFiles[f])]
            : [.. pakFiles.Values];

        ExtractFiles(outputDir, filesToExtract, copyRaw);

        return 0;
    }

    private static void ExtractFiles(string outputDir, List<PakFileEntry> files, bool copyRaw)
    {
        var progress = AnsiConsole.Progress()
                    .HideCompleted(false)
                    .Columns([
                        new TaskDescriptionColumn(),
                        new ProgressBarColumn(),
                        new PercentageColumn(),
                        new SpinnerColumn()
                    ]);

        progress.Start(ctx =>
        {
            var task = ctx.AddTask("Extracting Files", maxValue: files.Count);

            foreach (var file in files)
            {
                ExtractFile(outputDir, file, copyRaw);
                task.Increment(1);
            }

        });
    }

    private static void ExtractFile(string outputDir, PakFileEntry file, bool copyRaw)
    {
        var folder = Path.GetDirectoryName(file.Path);
        var fileName = Path.GetFileName(file.Path);
        var fullPath = Path.Combine(outputDir, folder!);

        Directory.CreateDirectory(fullPath);
        using var output = File.Create(Path.Combine(fullPath, fileName));

        if (copyRaw)
        {
            WriteUncompressedFile(output, file);
        }
        else
        {
            switch (file.CompressionType)
            {
                case 0:
                    WriteUncompressedFile(output, file);
                    break;
                case 0x106: //  File is compressed using the DEFLATE algorithm
                    WriteZLibCompressedFile(output, file);
                    break;
                default:
                    throw new Exception($"Unknown compression type: ${file.CompressionType} in file ${file.Path}");
            }
        }
    }

    private static void WriteUncompressedFile(FileStream output, PakFileEntry file)
    {
        using var input = new MemoryStream(file.Data.Value);
        input.CopyTo(output);
    }

    private static void WriteZLibCompressedFile(FileStream output, PakFileEntry file)
    {
        using var compressed = new MemoryStream(file.Data.Value[2..]); // Skip zlib header
        using var deflate = new DeflateStream(compressed, CompressionMode.Decompress);
        deflate.CopyTo(output);
    }

}