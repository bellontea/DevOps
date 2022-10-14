import argparse
import os
import yaml
import re
from datetime import datetime
import shutil

# Получение следующего разделителя (не рассматривает внутренние кавычки)
def get_next_sep(string, find_sep, start_sep=None):
    count = 1
    find_iter = 0
    for word in string:
        find_iter += 1
        if word == find_sep:
            count -= 1
        if word == start_sep:
            count += 1
        if not count:
            break
    return find_iter

# Проверка времени создания директории
def check_time(expire_time, format_time, check_file_name, file):
    file = re.search(check_file_name, file)
    if not file:
        return False
    file = file[0]
    (mode, ino, dev, nlink, uid, gid, size, atime, mtime, ctime) = os.stat(file)
    date_create = datetime.fromtimestamp(mtime)
    date_expire = datetime.strptime(expire_time, format_time)
    return date_create > date_expire


def for_each(value, func_list):
    for func in func_list:
        value = func(value)
    return value

# Класс, инициализирующий данные конфигурации
class ParserConfig:
    def __init__(self, config_path):
        with open(config_path, "r") as file:
            yaml_rule = yaml.safe_load(file)
        if not yaml_rule:
            raise Exception("Can't read using yaml")
        self.general = yaml_rule.get("general")
        self.value = yaml_rule.get("value")
        rules = yaml_rule.get("rule_del")

        if not all([self.general, self.value, rules]):
            raise Exception("Not all keys exist")
        # Конвеер функций для преобразования правил
        parser_rule = [self.replace_var, self.replace_reg, self.parse_rule]
        self.rule = [for_each(rule, parser_rule) for rule in rules]

    # Проверка наличия отрицания в правиле
    def check_not(self, rule):
        rule = rule.strip()
        nof_f = False
        if rule.startswith(self.general["NOT"]):
            nof_f = True
            rule = rule[len(self.general["NOT"]):].strip()
        return rule, nof_f

    # Обработка регулярных выражений внутри правила
    def replace_reg(self, rule):
        sep = self.general["regular_start"]
        len_sep = len(sep)
        reg_start = rule.find(sep)
        if reg_start == -1:
            return rule
        reg_start = reg_start + len_sep + 1
        reg_end = get_next_sep(rule[reg_start:], '}', '{')
        reg_str = rule[reg_start:reg_start + reg_end - 1]
        new_reg_str = reg_str
        while new_reg_str.find(sep + '{') != -1:
            new_reg_str = new_reg_str.replace(sep + '{', '', 1)
            last = new_reg_str.rfind("}")
            new_reg_str = new_reg_str[:last] + new_reg_str[last + 1:]
        rule = rule.replace(reg_str, new_reg_str)
        reg_end = reg_start + get_next_sep(rule[reg_start:], '}', '{')
        rule = rule[:reg_end] + self.replace_reg(rule[reg_end:])
        return rule

    # Замена названия переменной на ее значение
    def replace_var(self, rule):
        find_iter = rule.find(self.general["value_start"] + '{')
        len_sep = len(self.general["value_start"])
        while find_iter != -1:
            value = rule[find_iter + len_sep + 1:find_iter + len_sep + get_next_sep(rule[find_iter + len_sep + 1:],
                                                                                    "}", "{")]
            rule = rule.replace(f"{self.general['value_start']}"
                                "{"
                                f"{value}"
                                "}", self.value[value])
            find_iter = rule.find(self.general["value_start"] + '{')
        return rule

    # Преобразование строчной команды в функцию
    def parse_rule(self, rule):
        rule, nof_f = self.check_not(rule)
        left_rule = None
        # Разделение в логических выражениях
        if rule[0] == "(":
            left_rule = rule[1:get_next_sep(rule[1:], ")", "(")]
        else:
            # Поиск разделителей команд
            test_str = f"( {self.general['AND']} | {self.general['OR']} )"
            test = re.findall(test_str, rule)
            if test:
                separator = next(x.strip()
                                 for x in test
                                 if x.strip() in [self.general["AND"], self.general["OR"]])
                if separator:
                    left_rule = rule[:rule.find(separator) - 1]
        if not left_rule:
            # Если не полчилось разделить на команды, значит, осталась одна команда
            # Генерация функции проверки регулярного выражения или функции проверки даты создания файла
            if rule[0] == self.general["regular_start"]:
                return lambda x: nof_f ^ bool(re.fullmatch(self.general["dir_path"] + "/" + rule[2:len(rule) - 1], x))
            if rule[0] == self.general["EXPIRE"]:
                separator = rule.find("(")
                len_sep = len(self.general["EXPIRE"])
                return lambda x: nof_f ^ check_time(rule[2:len_sep + get_next_sep(rule[len_sep + 1:], "}", "{")],
                                                    self.general["time_format"],
                                                    self.general["dir_path"] + "/" +
                                                    rule[separator + 3:separator + get_next_sep(rule[separator:], ")", "(") - 2],
                                                    x)
            raise Exception("Error")

        rule = rule[len(left_rule):]
        if nof_f:
            left_rule = f'{self.general["NOT"]} {left_rule}'
            nof_f = False
        result = self.parse_rule(left_rule)
        while not rule[0].isspace():
            rule = rule[1:]
        rule, nof_f = self.check_not(rule)
        # Создание функции в зависимости от условного оператора
        if rule.startswith(self.general["AND"]):
            right_result = self.parse_rule(rule[len(self.general["AND"]):])
            return lambda x: result(x) and nof_f ^ right_result(x)
        if rule.startswith(self.general["OR"]):
            right_result = self.parse_rule(rule[len(self.general["OR"]):])
            return lambda x: result(x) or nof_f ^ right_result(x)
        raise Exception("ERROR")

    # Проверка файла на соответствие заданных правил
    def check(self, path_file):
        for rule in self.rule:
            if rule(path_file):
                return True
        return False


def main(config_file):
    if not os.path.exists(config_file):
        raise Exception("Config not exists")
    parse = ParserConfig(config_file)
    for root, dirs, files in os.walk(parse.general["dir_path"]):
        for file in files:
            file = os.path.join(root, file)
            if parse.check(file):
                print(file)
                os.remove(file)


if __name__ == '__main__':
    # argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--yaml_config', default="settings.yaml")
    args = parser.parse_args()
    main(args.yaml_config)
