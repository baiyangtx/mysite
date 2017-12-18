---
title: XML 文件出现 Input is not porper UTF-8, indicate encoding ! 解决办法 
date: 2017-12-18 
---

最近项目中用 xStream 框架生成的 XML 用 Chrome 浏览器打开后报了 `Input is not porper UTF-8, indicate encoding !` 效果如下

![image](/postimgs/xml-not-porper-utf8/xml.jpg)

其产生的原因是构成XML文件的字符中出现了XML标准中禁止出现的字符。XML标准中禁止文件的字符流中出现如下字符：

```
#x0 - #x8 (ASCII 0 - 8)
#xB - #xC (ASCII 11 - 12)
#xE - #x1F (ASCII 14 - 31)
```

这些字符都是ASCII 码中的一些转义字符，在代码中过滤掉就好。数据库中出现这类字符的一个很大可能的原因是在MySQL的utf8 表中存入了 Emoji 表情。如果你的数据库中可能会存入Emoji表情，请设置数据库的存储格式为 utf8mb4 格式。

可以使用如下的Java代码片段用于处理这种 XML文件。

```java
public static String removeIllegalXmlCharacter(String xml ){
    return xml.replaceAll(
            "\\u0000|\\u0001|\\u0002|\\u0003|\\u0004|\\u0005|" +
                    "\\u0006|\\u0007|\\u0008|\\u0009|\\u000a|\\u000b|" +
                    "\\u000c|\\u000d|\\u000e|\\u000f|\\u0010|\\u0011|\\u0012|" +
                    "\\u0013|\\u0014|\\u0015|\\u0016|\\u0017|\\u0018|\\u0019|" +
                    "\\u001a|\\u001b|\\u001c|\\u001d|\\u001e|\\u001f","");
}
```

