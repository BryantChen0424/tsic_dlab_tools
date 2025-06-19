# 可自行更改以下參數
TOP       = hello
SRC_V     = $(TOP).v
SRC_CPP   = $(TOP).cpp
EXEC      = V$(TOP)
OBJ_DIR   = obj_dir
VCD       = wave.vcd

# verilator 的 flags
VERILATOR_FLAGS = --trace

.PHONY: all dut test clean

# make all：產生執行檔
all: $(OBJ_DIR)/$(EXEC)

# make dut：只產生 verilated C++ 模型（不編譯 testbench）
dut:
	verilator $(VERILATOR_FLAGS) --cc $(SRC_V)

# make test：執行模擬（包含編譯）
test: all
	./$(OBJ_DIR)/$(EXEC)

# 模型 + testbench 一起編譯成執行檔
$(OBJ_DIR)/$(EXEC): $(SRC_V) $(SRC_CPP)
	verilator $(VERILATOR_FLAGS) --cc $(SRC_V) --exe $(SRC_CPP) --build

# make clean：清除所有生成檔
clean:
	rm -rf $(OBJ_DIR) $(VCD) *.txt
