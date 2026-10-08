# Náhled z opravdových SSE delta rámců; neúplný JSON nikdy nespouští nákup.
load json
load aikit as ai

context = {}

function start(callback, fields=None):
    if fields == None:
        fields = []
    end
    context["line"] = ByteArray()
    context["callback"] = callback
    context["fields"] = fields
    context["done"] = False
    context["bytes"] = 0
    context["depth"] = 0
    context["expect_key"] = False
    context["in_string"] = False
    context["is_key"] = False
    context["capture"] = False
    context["key"] = ""
    context["key_text"] = ""
    context["escape"] = False
    context["unicode_left"] = 0
    context["unicode_value"] = 0
    context["surrogate"] = 0
    context["pending"] = ""
end

function flush():
    if context["pending"] != "":
        text = context["pending"]
        context["pending"] = ""
        callback = context["callback"]
        call callback(text)
    end
end

function decoded(character):
    if context["is_key"]:
        context["key_text"] += character
    elif context["capture"]:
        context["pending"] += character
    end
end

function arguments(fragment):
    for character in fragment:
        if context["in_string"]:
            if context["unicode_left"] > 0:
                digit = "0123456789abcdef".find(character.lower())
                if digit < 0:
                    Error(StreamError: "Neplatný unicode escape v náhledu")
                end
                context["unicode_value"] = context["unicode_value"] * 16 + digit
                context["unicode_left"] -= 1
                if context["unicode_left"] == 0:
                    code = context["unicode_value"]
                    if code >= 55296 and code <= 56319:
                        context["surrogate"] = code
                    elif code >= 56320 and code <= 57343 and context["surrogate"] != 0:
                        call decoded(chr(65536 + (context["surrogate"] - 55296) * 1024 + code - 56320))
                        context["surrogate"] = 0
                    elif code < 55296 or code > 57343:
                        call decoded(chr(code))
                    end
                end
            elif context["escape"]:
                context["escape"] = False
                if character == "u":
                    context["unicode_left"] = 4
                    context["unicode_value"] = 0
                elif character == "n":
                    call decoded(chr(10))
                elif character == "r":
                    call decoded(chr(13))
                elif character == "t":
                    call decoded(chr(9))
                elif character == "b":
                    call decoded(chr(8))
                elif character == "f":
                    call decoded(chr(12))
                else:
                    call decoded(character)
                end
            elif character == chr(92):
                context["escape"] = True
            elif character == chr(34):
                context["in_string"] = False
                if context["is_key"]:
                    context["key"] = context["key_text"]
                end
                context["capture"] = False
            else:
                call decoded(character)
            end
        elif character == chr(34):
            context["in_string"] = True
            context["is_key"] = context["depth"] == 1 and context["expect_key"]
            context["key_text"] = ""
            context["capture"] = not context["is_key"] and context["key"] in context["fields"] and (context["depth"] == 1 or (context["depth"] == 2 and context["key"] == "ideas"))
            if context["capture"]:
                if context["key"] == "ideas":
                    context["pending"] += chr(10) + "• "
                elif context["key"] != "message":
                    context["pending"] += chr(10) + chr(10)
                    if context["key"] == "code":
                        context["pending"] += "Python kód:" + chr(10)
                    elif context["key"] == "tests":
                        context["pending"] += "Testy:" + chr(10)
                    end
                end
            end
        elif character == "{" or character == "[":
            context["depth"] += 1
            if context["depth"] == 1:
                context["expect_key"] = True
            end
        elif character == "}" or character == "]":
            context["depth"] -= 1
        elif character == ":" and context["depth"] == 1:
            context["expect_key"] = False
        elif character == "," and context["depth"] == 1:
            context["expect_key"] = True
            context["key"] = ""
        end
    end
    call flush()
end

function event(text):
    if text == "[DONE]":
        context["done"] = True
        return
    end
    data = json.decode(text)
    if "error" in data:
        Error(StreamError: "Přenos dodávky selhal")
    end
    for choice in data.get("choices", []):
        delta = choice.get("delta", {})
        if len(context["fields"]) == 0 and type(delta.get("content", None)) == "String":
            callback = context["callback"]
            call callback(delta["content"])
        end
        for item in delta.get("tool_calls", []):
            # Náhled pouze prvního vynuceného nástroje; úplný počet ověří aikit.
            if item.get("index", 0) == 0:
                call arguments(item.get("function", {}).get("arguments", ""))
            end
        end
    end
end

function receive(chunk):
    unsafe:
        count = __lsl_text_byte_length(String(chunk))
    end
    context["bytes"] += count
    if context["bytes"] > 2000000:
        Error(StreamError: "Přenos překročil limit")
    end
    for index in rang(count):
        unsafe:
            byte = __lsl_text_byte(String(chunk), index)
        end
        if byte == 10:
            data = ByteArray(context["line"])
            unsafe:
                line = __lsl_text_from_bytes(data, 0, len(data))
            end
            context["line"] = ByteArray()
            if line[0:5] == "data:":
                call event(line[5:].strip())
            end
        elif byte != 13:
            context["line"].append(byte)
            if len(context["line"]) > 100000:
                Error(StreamError: "Rámec přenosu překročil limit")
            end
        end
    end
end

function ignore(text):
    return None
end

function model(endpoint, token, messages, tools, name, tokens, timeout, fields, callback, cancel=None):
    call start(callback, fields)
    result = ai.stream(endpoint, token, "flash", messages, {"timeout": timeout, "retries": 0, "temperature": 0, "max_tokens": tokens, "tools": tools, "tool_choice": {"type": "function", "function": {"name": name}}, "stream_reliability": "live", "transport_chunk": receive, "cancel": cancel}, ignore)
    return result["tool_calls"]
end
