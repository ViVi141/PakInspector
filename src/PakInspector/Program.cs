using PakInspector.Commands;
using System.CommandLine;

var rootCommand = new RootCommand("Viewer and extractor for Arma Reforger .pak files");
rootCommand.Subcommands.Add(ChunksCommand.Command);
rootCommand.Subcommands.Add(PakInspectCommand.Command);
rootCommand.Subcommands.Add(PakExtractCommand.Command);

return rootCommand.Parse(args).Invoke();
