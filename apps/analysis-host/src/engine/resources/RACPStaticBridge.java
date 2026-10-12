// Fixed operations for the local RACP Ghidra adapter. No arbitrary script input.
// @category RACP

import com.google.gson.*;
import java.nio.charset.StandardCharsets;
import java.nio.file.*;
import ghidra.app.util.headless.HeadlessScript;
import ghidra.app.decompiler.*;
import ghidra.program.model.address.Address;
import ghidra.program.model.data.StringDataInstance;
import ghidra.program.model.listing.*;
import ghidra.program.model.symbol.*;

public class RACPStaticBridge extends HeadlessScript {
    private static final Gson JSON = new Gson();
    private static final int MAX_OUTPUT = 512 * 1024;
    private String executionState = "not_started";

    private String hex(Address address) {
        return "0x" + Long.toUnsignedString(address.getOffset(), 16);
    }

    private JsonObject object(String key, String value) {
        JsonObject result = new JsonObject();
        result.addProperty(key, value);
        return result;
    }

    private JsonObject info() {
        JsonObject result = object("architecture", currentProgram.getLanguage().getLanguageID().toString());
        result.addProperty("image_base", hex(currentProgram.getImageBase()));
        result.addProperty("program_name", currentProgram.getName());
        result.addProperty("function_count", currentProgram.getFunctionManager().getFunctionCount());
        result.addProperty("analysis_state", "READY");
        return result;
    }

    private Address address(JsonObject input) throws Exception {
        long value = Long.parseUnsignedLong(input.get("address").getAsString().substring(2), 16);
        if (input.has("address_kind") && input.get("address_kind").getAsString().equals("module_rva")) {
            if (value < 0) throw new IllegalArgumentException("module RVA exceeds the supported range");
            if (!input.get("module").getAsString().equals(currentProgram.getName())) {
                throw new IllegalArgumentException("module does not match the analyzed program");
            }
            return currentProgram.getImageBase().addNoWrap(value);
        }
        return currentProgram.getAddressFactory().getDefaultAddressSpace().getAddress(value);
    }

    private JsonObject page(JsonArray rows, String cursor) {
        JsonObject result = new JsonObject();
        result.add("items", rows);
        result.add("next_cursor", cursor == null ? JsonNull.INSTANCE : new JsonPrimitive(cursor));
        return result;
    }

    private JsonObject listing(JsonObject input, boolean strings) throws Exception {
        int limit = input.get("limit").getAsInt();
        String cursor = input.has("cursor") && !input.get("cursor").isJsonNull()
            ? input.get("cursor").getAsString() : null;
        Address after = cursor == null ? null
            : currentProgram.getAddressFactory().getDefaultAddressSpace().getAddress(cursor);
        JsonArray rows = new JsonArray();
        String last = null;
        int visited = 0;
        if (!strings) {
            FunctionIterator functions = currentProgram.getFunctionManager().getFunctions(true);
            while (functions.hasNext()) {
                monitor.checkCancelled();
                if (++visited > 1000000) throw new IllegalStateException("listing scan limit exceeded");
                Function function = functions.next();
                Address entry = function.getEntryPoint();
                if (after != null && entry.compareTo(after) <= 0) continue;
                if (rows.size() == limit) return page(rows, last);
                JsonObject row = object("address", hex(entry));
                row.addProperty("name", function.getName());
                row.addProperty("size_bytes", function.getBody().getNumAddresses());
                row.addProperty("signature", function.getPrototypeString(false, false));
                rows.add(row);
                last = entry.toString();
            }
        } else {
            DataIterator data = currentProgram.getListing().getDefinedData(true);
            while (data.hasNext()) {
                monitor.checkCancelled();
                if (++visited > 1000000) throw new IllegalStateException("listing scan limit exceeded");
                Data item = data.next();
                Address entry = item.getAddress();
                if (after != null && entry.compareTo(after) <= 0) continue;
                if (!StringDataInstance.isString(item)) continue;
                if (rows.size() == limit) return page(rows, last);
                String text = StringDataInstance.getStringDataInstance(item).getStringValue();
                if (text == null) continue;
                JsonObject row = object("address", hex(entry));
                row.addProperty("value", text.substring(0, Math.min(text.length(), 8192)));
                row.addProperty("truncated", text.length() > 8192);
                row.addProperty("size_bytes", item.getLength());
                rows.add(row);
                last = entry.toString();
            }
        }
        return page(rows, null);
    }

    private JsonObject xrefs(JsonObject input) throws Exception {
        Address location = address(input);
        int limit = input.get("limit").getAsInt();
        int offset = input.has("cursor") && !input.get("cursor").isJsonNull()
            ? Integer.parseInt(input.get("cursor").getAsString()) : 0;
        if (offset < 0 || offset > 1000000) throw new IllegalArgumentException("invalid xref cursor");
        ReferenceIterator refs = currentProgram.getReferenceManager().getReferencesTo(location);
        JsonArray rows = new JsonArray();
        int index = 0;
        while (refs.hasNext()) {
            monitor.checkCancelled();
            Reference ref = refs.next();
            if (index++ < offset) continue;
            if (rows.size() == limit) return page(rows, Integer.toString(index - 1));
            JsonObject row = object("from", hex(ref.getFromAddress()));
            row.addProperty("to", hex(ref.getToAddress()));
            row.addProperty("type", ref.getReferenceType().toString());
            rows.add(row);
        }
        return page(rows, null);
    }

    private JsonObject disassemble(JsonObject input) throws Exception {
        Address start = address(input);
        int limit = input.get("limit").getAsInt();
        if (input.has("cursor") && !input.get("cursor").isJsonNull()) {
            start = currentProgram.getAddressFactory().getDefaultAddressSpace()
                .getAddress(input.get("cursor").getAsString());
        }
        InstructionIterator instructions = currentProgram.getListing().getInstructions(start, true);
        JsonArray rows = new JsonArray();
        while (instructions.hasNext()) {
            monitor.checkCancelled();
            Instruction instruction = instructions.next();
            if (rows.size() == limit) return page(rows, instruction.getAddress().toString());
            JsonObject row = object("address", hex(instruction.getAddress()));
            row.addProperty("instruction", instruction.toString());
            row.addProperty("size_bytes", instruction.getLength());
            row.addProperty("comment", currentProgram.getListing().getComment(
                CodeUnit.EOL_COMMENT, instruction.getAddress()));
            rows.add(row);
        }
        return page(rows, null);
    }

    private JsonObject decompile(JsonObject input) throws Exception {
        Function function = currentProgram.getFunctionManager().getFunctionContaining(address(input));
        if (function == null) throw new IllegalArgumentException("no function at the requested address");
        DecompInterface decompiler = new DecompInterface();
        try {
            decompiler.toggleSyntaxTree(false);
            if (!decompiler.openProgram(currentProgram)) throw new IllegalStateException("decompiler unavailable");
            DecompileResults result = decompiler.decompileFunction(function, 30, monitor);
            if (!result.decompileCompleted() || result.getDecompiledFunction() == null) {
                throw new IllegalStateException("decompile did not complete");
            }
            String text = result.getDecompiledFunction().getC();
            if (text.length() > 131072) throw new IllegalStateException("decompile output exceeds limit");
            JsonObject response = object("address", hex(function.getEntryPoint()));
            response.addProperty("name", function.getName());
            response.addProperty("text", text);
            response.addProperty("language", "c");
            return response;
        } finally {
            decompiler.dispose();
        }
    }

    private JsonObject command(JsonObject input) throws Exception {
        Address location = address(input);
        String action = input.get("action").getAsString();
        int transaction = currentProgram.startTransaction("RACP " + action);
        boolean success = false;
        executionState = "unknown";
        try {
            if (action.equals("rename")) {
                Function function = currentProgram.getFunctionManager().getFunctionAt(location);
                if (function == null) throw new IllegalArgumentException("rename requires a function entry");
                function.setName(input.get("name").getAsString(), SourceType.USER_DEFINED);
            } else if (action.equals("comment")) {
                currentProgram.getListing().setComment(location, CodeUnit.EOL_COMMENT,
                    input.get("text").getAsString());
            } else throw new IllegalArgumentException("unsupported mutation");
            success = true;
            JsonObject result = new JsonObject();
            result.addProperty("updated", true);
            return result;
        } finally {
            currentProgram.endTransaction(transaction, success);
            executionState = success ? "completed" : "not_started";
        }
    }

    @Override
    public void run() throws Exception {
        String[] args = getScriptArgs();
        if (args.length != 2) throw new IllegalArgumentException("request and response files required");
        Path output = Path.of(args[1]);
        JsonObject reply = new JsonObject();
        try {
            if (Files.size(Path.of(args[0])) > 65536) throw new IllegalArgumentException("request size limit");
            JsonObject input = JsonParser.parseString(Files.readString(Path.of(args[0]), StandardCharsets.UTF_8))
                .getAsJsonObject();
            if (analysisTimeoutOccurred()) throw new IllegalStateException("analysis deadline elapsed");
            String action = input.get("action").getAsString();
            JsonObject result;
            switch (action) {
                case "info": result = info(); break;
                case "functions": result = listing(input, false); break;
                case "strings": result = listing(input, true); break;
                case "xrefs": result = xrefs(input); break;
                case "disassemble": result = disassemble(input); break;
                case "decompile": result = decompile(input); break;
                case "rename": case "comment": result = command(input); break;
                default: throw new IllegalArgumentException("unsupported action");
            }
            reply.add("result", result);
            if (JSON.toJson(reply).getBytes(StandardCharsets.UTF_8).length > MAX_OUTPUT) {
                throw new IllegalStateException("response size limit exceeded");
            }
        } catch (Exception exception) {
            reply = new JsonObject();
            reply.addProperty("error", exception instanceof IllegalArgumentException
                ? "INVALID_ARGUMENT" : "GHIDRA_OPERATION_FAILED");
            reply.addProperty("message", exception.getClass().getSimpleName() + ": " + exception.getMessage());
            reply.addProperty("execution_state", executionState);
        }
        byte[] bytes = JSON.toJson(reply).getBytes(StandardCharsets.UTF_8);
        if (bytes.length > MAX_OUTPUT) throw new IllegalStateException("error response exceeds limit");
        Files.write(output, bytes, StandardOpenOption.CREATE_NEW, StandardOpenOption.WRITE);
    }
}
