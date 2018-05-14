---
title: python 实现 Json数据完整性校验——JsonSchema介绍
date: 2018-1-8
---

`JSON` 是一种灵活的数据类型，最早是被应用在`JavaScript`语言中，现在已经发展为一种独立于语言的数据格式，被应用在各个领域。说到 `JSON` ，人们会将其和`XML`数据格式进行比较，除了语法上的精简和繁杂以外，XML相比Json最大的一个特点就是拥有以`XML Schema (XSD)`和`DTD` 为代表的，一套完整的XML格式数据的完整性校验标准。这使得XML的数据结构很容易定义，也很容易的知道XML文档的正确性。

在定义复杂的 `JSON` 格式的数据接口时，通过硬编码的方式对数据完整性进行校验是一件非常痛苦的事情。[JsonSchema](http://json-schema.org/) 提供了一种类似于XML中的 XSD 和 DTD的方式编写Json文档的描述文档，文档可以对Json格式的数据进行约束，并且可以很方便的调用各个语言对应的库对 JSON 文档进行校验。

# Json-Schema 简单介绍

Json-Schema 是一份草案，截至到目前已经推出了7个版本，其定义了一套基于Json语法的JSON文档的描述方法。这里对其做一点简单的介绍，举个栗子，有这么一份Json文档

```json
{"name" : "Eggs", "price" : 34.99}
```
文档有两个属性，其中一个是字符串`string`类型，一个是数值`number`类型，其文档结构可以用如下的 Schema 描述：

```json
{
    "$schema": "http://json-schema.org/draft-04/schema#",
    "title" : "示例对象" ,
    "description" : "为了描述JsonSchema举得栗子",
    "type" : "object", 
    "properties" : {
        "name" : {
            "type" : "string" ,
        },
        "price" : {
            "type" : "number" 
        }
    },
    "required" : ["name","price"]
}
```

Scm
