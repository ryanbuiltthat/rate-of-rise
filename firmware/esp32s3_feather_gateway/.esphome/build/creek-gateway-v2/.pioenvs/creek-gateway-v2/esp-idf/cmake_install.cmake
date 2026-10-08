# Install script for directory: C:/Users/risfo/.platformio/packages/framework-espidf

# Set the install prefix
if(NOT DEFINED CMAKE_INSTALL_PREFIX)
  set(CMAKE_INSTALL_PREFIX "C:/Program Files (x86)/creek-gateway-v2")
endif()
string(REGEX REPLACE "/$" "" CMAKE_INSTALL_PREFIX "${CMAKE_INSTALL_PREFIX}")

# Set the install configuration name.
if(NOT DEFINED CMAKE_INSTALL_CONFIG_NAME)
  if(BUILD_TYPE)
    string(REGEX REPLACE "^[^A-Za-z0-9_]+" ""
           CMAKE_INSTALL_CONFIG_NAME "${BUILD_TYPE}")
  else()
    set(CMAKE_INSTALL_CONFIG_NAME "")
  endif()
  message(STATUS "Install configuration: \"${CMAKE_INSTALL_CONFIG_NAME}\"")
endif()

# Set the component getting installed.
if(NOT CMAKE_INSTALL_COMPONENT)
  if(COMPONENT)
    message(STATUS "Install component: \"${COMPONENT}\"")
    set(CMAKE_INSTALL_COMPONENT "${COMPONENT}")
  else()
    set(CMAKE_INSTALL_COMPONENT)
  endif()
endif()

# Is this installation the result of a crosscompile?
if(NOT DEFINED CMAKE_CROSSCOMPILING)
  set(CMAKE_CROSSCOMPILING "TRUE")
endif()

# Set path to fallback-tool for dependency-resolution.
if(NOT DEFINED CMAKE_OBJDUMP)
  set(CMAKE_OBJDUMP "C:/Users/risfo/.platformio/packages/toolchain-xtensa-esp-elf/bin/xtensa-esp32s3-elf-objdump.exe")
endif()

if(NOT CMAKE_INSTALL_LOCAL_ONLY)
  # Include the install script for the subdirectory.
  include("C:/1_ProjectRepos/rate-of-rise/firmware/esp32s3_feather_gateway/.esphome/build/creek-gateway-v2/.pioenvs/creek-gateway-v2/esp-idf/xtensa/cmake_install.cmake")
endif()

if(NOT CMAKE_INSTALL_LOCAL_ONLY)
  # Include the install script for the subdirectory.
  include("C:/1_ProjectRepos/rate-of-rise/firmware/esp32s3_feather_gateway/.esphome/build/creek-gateway-v2/.pioenvs/creek-gateway-v2/esp-idf/esp_driver_gpio/cmake_install.cmake")
endif()

if(NOT CMAKE_INSTALL_LOCAL_ONLY)
  # Include the install script for the subdirectory.
  include("C:/1_ProjectRepos/rate-of-rise/firmware/esp32s3_feather_gateway/.esphome/build/creek-gateway-v2/.pioenvs/creek-gateway-v2/esp-idf/esp_timer/cmake_install.cmake")
endif()

if(NOT CMAKE_INSTALL_LOCAL_ONLY)
  # Include the install script for the subdirectory.
  include("C:/1_ProjectRepos/rate-of-rise/firmware/esp32s3_feather_gateway/.esphome/build/creek-gateway-v2/.pioenvs/creek-gateway-v2/esp-idf/esp_pm/cmake_install.cmake")
endif()

if(NOT CMAKE_INSTALL_LOCAL_ONLY)
  # Include the install script for the subdirectory.
  include("C:/1_ProjectRepos/rate-of-rise/firmware/esp32s3_feather_gateway/.esphome/build/creek-gateway-v2/.pioenvs/creek-gateway-v2/esp-idf/mbedtls/cmake_install.cmake")
endif()

if(NOT CMAKE_INSTALL_LOCAL_ONLY)
  # Include the install script for the subdirectory.
  include("C:/1_ProjectRepos/rate-of-rise/firmware/esp32s3_feather_gateway/.esphome/build/creek-gateway-v2/.pioenvs/creek-gateway-v2/esp-idf/bootloader/cmake_install.cmake")
endif()

if(NOT CMAKE_INSTALL_LOCAL_ONLY)
  # Include the install script for the subdirectory.
  include("C:/1_ProjectRepos/rate-of-rise/firmware/esp32s3_feather_gateway/.esphome/build/creek-gateway-v2/.pioenvs/creek-gateway-v2/esp-idf/esptool_py/cmake_install.cmake")
endif()

if(NOT CMAKE_INSTALL_LOCAL_ONLY)
  # Include the install script for the subdirectory.
  include("C:/1_ProjectRepos/rate-of-rise/firmware/esp32s3_feather_gateway/.esphome/build/creek-gateway-v2/.pioenvs/creek-gateway-v2/esp-idf/partition_table/cmake_install.cmake")
endif()

if(NOT CMAKE_INSTALL_LOCAL_ONLY)
  # Include the install script for the subdirectory.
  include("C:/1_ProjectRepos/rate-of-rise/firmware/esp32s3_feather_gateway/.esphome/build/creek-gateway-v2/.pioenvs/creek-gateway-v2/esp-idf/esp_app_format/cmake_install.cmake")
endif()

if(NOT CMAKE_INSTALL_LOCAL_ONLY)
  # Include the install script for the subdirectory.
  include("C:/1_ProjectRepos/rate-of-rise/firmware/esp32s3_feather_gateway/.esphome/build/creek-gateway-v2/.pioenvs/creek-gateway-v2/esp-idf/esp_bootloader_format/cmake_install.cmake")
endif()

if(NOT CMAKE_INSTALL_LOCAL_ONLY)
  # Include the install script for the subdirectory.
  include("C:/1_ProjectRepos/rate-of-rise/firmware/esp32s3_feather_gateway/.esphome/build/creek-gateway-v2/.pioenvs/creek-gateway-v2/esp-idf/app_update/cmake_install.cmake")
endif()

if(NOT CMAKE_INSTALL_LOCAL_ONLY)
  # Include the install script for the subdirectory.
  include("C:/1_ProjectRepos/rate-of-rise/firmware/esp32s3_feather_gateway/.esphome/build/creek-gateway-v2/.pioenvs/creek-gateway-v2/esp-idf/esp_partition/cmake_install.cmake")
endif()

if(NOT CMAKE_INSTALL_LOCAL_ONLY)
  # Include the install script for the subdirectory.
  include("C:/1_ProjectRepos/rate-of-rise/firmware/esp32s3_feather_gateway/.esphome/build/creek-gateway-v2/.pioenvs/creek-gateway-v2/esp-idf/efuse/cmake_install.cmake")
endif()

if(NOT CMAKE_INSTALL_LOCAL_ONLY)
  # Include the install script for the subdirectory.
  include("C:/1_ProjectRepos/rate-of-rise/firmware/esp32s3_feather_gateway/.esphome/build/creek-gateway-v2/.pioenvs/creek-gateway-v2/esp-idf/bootloader_support/cmake_install.cmake")
endif()

if(NOT CMAKE_INSTALL_LOCAL_ONLY)
  # Include the install script for the subdirectory.
  include("C:/1_ProjectRepos/rate-of-rise/firmware/esp32s3_feather_gateway/.esphome/build/creek-gateway-v2/.pioenvs/creek-gateway-v2/esp-idf/esp_mm/cmake_install.cmake")
endif()

if(NOT CMAKE_INSTALL_LOCAL_ONLY)
  # Include the install script for the subdirectory.
  include("C:/1_ProjectRepos/rate-of-rise/firmware/esp32s3_feather_gateway/.esphome/build/creek-gateway-v2/.pioenvs/creek-gateway-v2/esp-idf/spi_flash/cmake_install.cmake")
endif()

if(NOT CMAKE_INSTALL_LOCAL_ONLY)
  # Include the install script for the subdirectory.
  include("C:/1_ProjectRepos/rate-of-rise/firmware/esp32s3_feather_gateway/.esphome/build/creek-gateway-v2/.pioenvs/creek-gateway-v2/esp-idf/esp_system/cmake_install.cmake")
endif()

if(NOT CMAKE_INSTALL_LOCAL_ONLY)
  # Include the install script for the subdirectory.
  include("C:/1_ProjectRepos/rate-of-rise/firmware/esp32s3_feather_gateway/.esphome/build/creek-gateway-v2/.pioenvs/creek-gateway-v2/esp-idf/esp_common/cmake_install.cmake")
endif()

if(NOT CMAKE_INSTALL_LOCAL_ONLY)
  # Include the install script for the subdirectory.
  include("C:/1_ProjectRepos/rate-of-rise/firmware/esp32s3_feather_gateway/.esphome/build/creek-gateway-v2/.pioenvs/creek-gateway-v2/esp-idf/esp_rom/cmake_install.cmake")
endif()

if(NOT CMAKE_INSTALL_LOCAL_ONLY)
  # Include the install script for the subdirectory.
  include("C:/1_ProjectRepos/rate-of-rise/firmware/esp32s3_feather_gateway/.esphome/build/creek-gateway-v2/.pioenvs/creek-gateway-v2/esp-idf/hal/cmake_install.cmake")
endif()

if(NOT CMAKE_INSTALL_LOCAL_ONLY)
  # Include the install script for the subdirectory.
  include("C:/1_ProjectRepos/rate-of-rise/firmware/esp32s3_feather_gateway/.esphome/build/creek-gateway-v2/.pioenvs/creek-gateway-v2/esp-idf/log/cmake_install.cmake")
endif()

if(NOT CMAKE_INSTALL_LOCAL_ONLY)
  # Include the install script for the subdirectory.
  include("C:/1_ProjectRepos/rate-of-rise/firmware/esp32s3_feather_gateway/.esphome/build/creek-gateway-v2/.pioenvs/creek-gateway-v2/esp-idf/heap/cmake_install.cmake")
endif()

if(NOT CMAKE_INSTALL_LOCAL_ONLY)
  # Include the install script for the subdirectory.
  include("C:/1_ProjectRepos/rate-of-rise/firmware/esp32s3_feather_gateway/.esphome/build/creek-gateway-v2/.pioenvs/creek-gateway-v2/esp-idf/soc/cmake_install.cmake")
endif()

if(NOT CMAKE_INSTALL_LOCAL_ONLY)
  # Include the install script for the subdirectory.
  include("C:/1_ProjectRepos/rate-of-rise/firmware/esp32s3_feather_gateway/.esphome/build/creek-gateway-v2/.pioenvs/creek-gateway-v2/esp-idf/esp_security/cmake_install.cmake")
endif()

if(NOT CMAKE_INSTALL_LOCAL_ONLY)
  # Include the install script for the subdirectory.
  include("C:/1_ProjectRepos/rate-of-rise/firmware/esp32s3_feather_gateway/.esphome/build/creek-gateway-v2/.pioenvs/creek-gateway-v2/esp-idf/esp_hw_support/cmake_install.cmake")
endif()

if(NOT CMAKE_INSTALL_LOCAL_ONLY)
  # Include the install script for the subdirectory.
  include("C:/1_ProjectRepos/rate-of-rise/firmware/esp32s3_feather_gateway/.esphome/build/creek-gateway-v2/.pioenvs/creek-gateway-v2/esp-idf/freertos/cmake_install.cmake")
endif()

if(NOT CMAKE_INSTALL_LOCAL_ONLY)
  # Include the install script for the subdirectory.
  include("C:/1_ProjectRepos/rate-of-rise/firmware/esp32s3_feather_gateway/.esphome/build/creek-gateway-v2/.pioenvs/creek-gateway-v2/esp-idf/newlib/cmake_install.cmake")
endif()

if(NOT CMAKE_INSTALL_LOCAL_ONLY)
  # Include the install script for the subdirectory.
  include("C:/1_ProjectRepos/rate-of-rise/firmware/esp32s3_feather_gateway/.esphome/build/creek-gateway-v2/.pioenvs/creek-gateway-v2/esp-idf/pthread/cmake_install.cmake")
endif()

if(NOT CMAKE_INSTALL_LOCAL_ONLY)
  # Include the install script for the subdirectory.
  include("C:/1_ProjectRepos/rate-of-rise/firmware/esp32s3_feather_gateway/.esphome/build/creek-gateway-v2/.pioenvs/creek-gateway-v2/esp-idf/cxx/cmake_install.cmake")
endif()

if(NOT CMAKE_INSTALL_LOCAL_ONLY)
  # Include the install script for the subdirectory.
  include("C:/1_ProjectRepos/rate-of-rise/firmware/esp32s3_feather_gateway/.esphome/build/creek-gateway-v2/.pioenvs/creek-gateway-v2/esp-idf/__pio_env/cmake_install.cmake")
endif()

if(NOT CMAKE_INSTALL_LOCAL_ONLY)
  # Include the install script for the subdirectory.
  include("C:/1_ProjectRepos/rate-of-rise/firmware/esp32s3_feather_gateway/.esphome/build/creek-gateway-v2/.pioenvs/creek-gateway-v2/esp-idf/esp_driver_gptimer/cmake_install.cmake")
endif()

if(NOT CMAKE_INSTALL_LOCAL_ONLY)
  # Include the install script for the subdirectory.
  include("C:/1_ProjectRepos/rate-of-rise/firmware/esp32s3_feather_gateway/.esphome/build/creek-gateway-v2/.pioenvs/creek-gateway-v2/esp-idf/esp_ringbuf/cmake_install.cmake")
endif()

if(NOT CMAKE_INSTALL_LOCAL_ONLY)
  # Include the install script for the subdirectory.
  include("C:/1_ProjectRepos/rate-of-rise/firmware/esp32s3_feather_gateway/.esphome/build/creek-gateway-v2/.pioenvs/creek-gateway-v2/esp-idf/esp_psram/cmake_install.cmake")
endif()

if(NOT CMAKE_INSTALL_LOCAL_ONLY)
  # Include the install script for the subdirectory.
  include("C:/1_ProjectRepos/rate-of-rise/firmware/esp32s3_feather_gateway/.esphome/build/creek-gateway-v2/.pioenvs/creek-gateway-v2/esp-idf/esp_driver_uart/cmake_install.cmake")
endif()

if(NOT CMAKE_INSTALL_LOCAL_ONLY)
  # Include the install script for the subdirectory.
  include("C:/1_ProjectRepos/rate-of-rise/firmware/esp32s3_feather_gateway/.esphome/build/creek-gateway-v2/.pioenvs/creek-gateway-v2/esp-idf/app_trace/cmake_install.cmake")
endif()

if(NOT CMAKE_INSTALL_LOCAL_ONLY)
  # Include the install script for the subdirectory.
  include("C:/1_ProjectRepos/rate-of-rise/firmware/esp32s3_feather_gateway/.esphome/build/creek-gateway-v2/.pioenvs/creek-gateway-v2/esp-idf/esp_event/cmake_install.cmake")
endif()

if(NOT CMAKE_INSTALL_LOCAL_ONLY)
  # Include the install script for the subdirectory.
  include("C:/1_ProjectRepos/rate-of-rise/firmware/esp32s3_feather_gateway/.esphome/build/creek-gateway-v2/.pioenvs/creek-gateway-v2/esp-idf/nvs_flash/cmake_install.cmake")
endif()

if(NOT CMAKE_INSTALL_LOCAL_ONLY)
  # Include the install script for the subdirectory.
  include("C:/1_ProjectRepos/rate-of-rise/firmware/esp32s3_feather_gateway/.esphome/build/creek-gateway-v2/.pioenvs/creek-gateway-v2/esp-idf/esp_phy/cmake_install.cmake")
endif()

if(NOT CMAKE_INSTALL_LOCAL_ONLY)
  # Include the install script for the subdirectory.
  include("C:/1_ProjectRepos/rate-of-rise/firmware/esp32s3_feather_gateway/.esphome/build/creek-gateway-v2/.pioenvs/creek-gateway-v2/esp-idf/esp_driver_usb_serial_jtag/cmake_install.cmake")
endif()

if(NOT CMAKE_INSTALL_LOCAL_ONLY)
  # Include the install script for the subdirectory.
  include("C:/1_ProjectRepos/rate-of-rise/firmware/esp32s3_feather_gateway/.esphome/build/creek-gateway-v2/.pioenvs/creek-gateway-v2/esp-idf/esp_vfs_console/cmake_install.cmake")
endif()

if(NOT CMAKE_INSTALL_LOCAL_ONLY)
  # Include the install script for the subdirectory.
  include("C:/1_ProjectRepos/rate-of-rise/firmware/esp32s3_feather_gateway/.esphome/build/creek-gateway-v2/.pioenvs/creek-gateway-v2/esp-idf/vfs/cmake_install.cmake")
endif()

if(NOT CMAKE_INSTALL_LOCAL_ONLY)
  # Include the install script for the subdirectory.
  include("C:/1_ProjectRepos/rate-of-rise/firmware/esp32s3_feather_gateway/.esphome/build/creek-gateway-v2/.pioenvs/creek-gateway-v2/esp-idf/lwip/cmake_install.cmake")
endif()

if(NOT CMAKE_INSTALL_LOCAL_ONLY)
  # Include the install script for the subdirectory.
  include("C:/1_ProjectRepos/rate-of-rise/firmware/esp32s3_feather_gateway/.esphome/build/creek-gateway-v2/.pioenvs/creek-gateway-v2/esp-idf/esp_netif_stack/cmake_install.cmake")
endif()

if(NOT CMAKE_INSTALL_LOCAL_ONLY)
  # Include the install script for the subdirectory.
  include("C:/1_ProjectRepos/rate-of-rise/firmware/esp32s3_feather_gateway/.esphome/build/creek-gateway-v2/.pioenvs/creek-gateway-v2/esp-idf/esp_netif/cmake_install.cmake")
endif()

if(NOT CMAKE_INSTALL_LOCAL_ONLY)
  # Include the install script for the subdirectory.
  include("C:/1_ProjectRepos/rate-of-rise/firmware/esp32s3_feather_gateway/.esphome/build/creek-gateway-v2/.pioenvs/creek-gateway-v2/esp-idf/wpa_supplicant/cmake_install.cmake")
endif()

if(NOT CMAKE_INSTALL_LOCAL_ONLY)
  # Include the install script for the subdirectory.
  include("C:/1_ProjectRepos/rate-of-rise/firmware/esp32s3_feather_gateway/.esphome/build/creek-gateway-v2/.pioenvs/creek-gateway-v2/esp-idf/esp_coex/cmake_install.cmake")
endif()

if(NOT CMAKE_INSTALL_LOCAL_ONLY)
  # Include the install script for the subdirectory.
  include("C:/1_ProjectRepos/rate-of-rise/firmware/esp32s3_feather_gateway/.esphome/build/creek-gateway-v2/.pioenvs/creek-gateway-v2/esp-idf/esp_wifi/cmake_install.cmake")
endif()

if(NOT CMAKE_INSTALL_LOCAL_ONLY)
  # Include the install script for the subdirectory.
  include("C:/1_ProjectRepos/rate-of-rise/firmware/esp32s3_feather_gateway/.esphome/build/creek-gateway-v2/.pioenvs/creek-gateway-v2/esp-idf/esp_driver_spi/cmake_install.cmake")
endif()

if(NOT CMAKE_INSTALL_LOCAL_ONLY)
  # Include the install script for the subdirectory.
  include("C:/1_ProjectRepos/rate-of-rise/firmware/esp32s3_feather_gateway/.esphome/build/creek-gateway-v2/.pioenvs/creek-gateway-v2/esp-idf/esp_gdbstub/cmake_install.cmake")
endif()

if(NOT CMAKE_INSTALL_LOCAL_ONLY)
  # Include the install script for the subdirectory.
  include("C:/1_ProjectRepos/rate-of-rise/firmware/esp32s3_feather_gateway/.esphome/build/creek-gateway-v2/.pioenvs/creek-gateway-v2/esp-idf/bt/cmake_install.cmake")
endif()

if(NOT CMAKE_INSTALL_LOCAL_ONLY)
  # Include the install script for the subdirectory.
  include("C:/1_ProjectRepos/rate-of-rise/firmware/esp32s3_feather_gateway/.esphome/build/creek-gateway-v2/.pioenvs/creek-gateway-v2/esp-idf/console/cmake_install.cmake")
endif()

if(NOT CMAKE_INSTALL_LOCAL_ONLY)
  # Include the install script for the subdirectory.
  include("C:/1_ProjectRepos/rate-of-rise/firmware/esp32s3_feather_gateway/.esphome/build/creek-gateway-v2/.pioenvs/creek-gateway-v2/esp-idf/http_parser/cmake_install.cmake")
endif()

if(NOT CMAKE_INSTALL_LOCAL_ONLY)
  # Include the install script for the subdirectory.
  include("C:/1_ProjectRepos/rate-of-rise/firmware/esp32s3_feather_gateway/.esphome/build/creek-gateway-v2/.pioenvs/creek-gateway-v2/esp-idf/esp-tls/cmake_install.cmake")
endif()

if(NOT CMAKE_INSTALL_LOCAL_ONLY)
  # Include the install script for the subdirectory.
  include("C:/1_ProjectRepos/rate-of-rise/firmware/esp32s3_feather_gateway/.esphome/build/creek-gateway-v2/.pioenvs/creek-gateway-v2/esp-idf/esp_driver_ana_cmpr/cmake_install.cmake")
endif()

if(NOT CMAKE_INSTALL_LOCAL_ONLY)
  # Include the install script for the subdirectory.
  include("C:/1_ProjectRepos/rate-of-rise/firmware/esp32s3_feather_gateway/.esphome/build/creek-gateway-v2/.pioenvs/creek-gateway-v2/esp-idf/esp_driver_bitscrambler/cmake_install.cmake")
endif()

if(NOT CMAKE_INSTALL_LOCAL_ONLY)
  # Include the install script for the subdirectory.
  include("C:/1_ProjectRepos/rate-of-rise/firmware/esp32s3_feather_gateway/.esphome/build/creek-gateway-v2/.pioenvs/creek-gateway-v2/esp-idf/esp_driver_isp/cmake_install.cmake")
endif()

if(NOT CMAKE_INSTALL_LOCAL_ONLY)
  # Include the install script for the subdirectory.
  include("C:/1_ProjectRepos/rate-of-rise/firmware/esp32s3_feather_gateway/.esphome/build/creek-gateway-v2/.pioenvs/creek-gateway-v2/esp-idf/esp_driver_cam/cmake_install.cmake")
endif()

if(NOT CMAKE_INSTALL_LOCAL_ONLY)
  # Include the install script for the subdirectory.
  include("C:/1_ProjectRepos/rate-of-rise/firmware/esp32s3_feather_gateway/.esphome/build/creek-gateway-v2/.pioenvs/creek-gateway-v2/esp-idf/esp_driver_i2c/cmake_install.cmake")
endif()

if(NOT CMAKE_INSTALL_LOCAL_ONLY)
  # Include the install script for the subdirectory.
  include("C:/1_ProjectRepos/rate-of-rise/firmware/esp32s3_feather_gateway/.esphome/build/creek-gateway-v2/.pioenvs/creek-gateway-v2/esp-idf/esp_driver_jpeg/cmake_install.cmake")
endif()

if(NOT CMAKE_INSTALL_LOCAL_ONLY)
  # Include the install script for the subdirectory.
  include("C:/1_ProjectRepos/rate-of-rise/firmware/esp32s3_feather_gateway/.esphome/build/creek-gateway-v2/.pioenvs/creek-gateway-v2/esp-idf/esp_driver_ledc/cmake_install.cmake")
endif()

if(NOT CMAKE_INSTALL_LOCAL_ONLY)
  # Include the install script for the subdirectory.
  include("C:/1_ProjectRepos/rate-of-rise/firmware/esp32s3_feather_gateway/.esphome/build/creek-gateway-v2/.pioenvs/creek-gateway-v2/esp-idf/esp_driver_parlio/cmake_install.cmake")
endif()

if(NOT CMAKE_INSTALL_LOCAL_ONLY)
  # Include the install script for the subdirectory.
  include("C:/1_ProjectRepos/rate-of-rise/firmware/esp32s3_feather_gateway/.esphome/build/creek-gateway-v2/.pioenvs/creek-gateway-v2/esp-idf/esp_driver_ppa/cmake_install.cmake")
endif()

if(NOT CMAKE_INSTALL_LOCAL_ONLY)
  # Include the install script for the subdirectory.
  include("C:/1_ProjectRepos/rate-of-rise/firmware/esp32s3_feather_gateway/.esphome/build/creek-gateway-v2/.pioenvs/creek-gateway-v2/esp-idf/esp_driver_sdio/cmake_install.cmake")
endif()

if(NOT CMAKE_INSTALL_LOCAL_ONLY)
  # Include the install script for the subdirectory.
  include("C:/1_ProjectRepos/rate-of-rise/firmware/esp32s3_feather_gateway/.esphome/build/creek-gateway-v2/.pioenvs/creek-gateway-v2/esp-idf/esp_driver_sdm/cmake_install.cmake")
endif()

if(NOT CMAKE_INSTALL_LOCAL_ONLY)
  # Include the install script for the subdirectory.
  include("C:/1_ProjectRepos/rate-of-rise/firmware/esp32s3_feather_gateway/.esphome/build/creek-gateway-v2/.pioenvs/creek-gateway-v2/esp-idf/sdmmc/cmake_install.cmake")
endif()

if(NOT CMAKE_INSTALL_LOCAL_ONLY)
  # Include the install script for the subdirectory.
  include("C:/1_ProjectRepos/rate-of-rise/firmware/esp32s3_feather_gateway/.esphome/build/creek-gateway-v2/.pioenvs/creek-gateway-v2/esp-idf/esp_driver_sdmmc/cmake_install.cmake")
endif()

if(NOT CMAKE_INSTALL_LOCAL_ONLY)
  # Include the install script for the subdirectory.
  include("C:/1_ProjectRepos/rate-of-rise/firmware/esp32s3_feather_gateway/.esphome/build/creek-gateway-v2/.pioenvs/creek-gateway-v2/esp-idf/esp_driver_sdspi/cmake_install.cmake")
endif()

if(NOT CMAKE_INSTALL_LOCAL_ONLY)
  # Include the install script for the subdirectory.
  include("C:/1_ProjectRepos/rate-of-rise/firmware/esp32s3_feather_gateway/.esphome/build/creek-gateway-v2/.pioenvs/creek-gateway-v2/esp-idf/esp_driver_tsens/cmake_install.cmake")
endif()

if(NOT CMAKE_INSTALL_LOCAL_ONLY)
  # Include the install script for the subdirectory.
  include("C:/1_ProjectRepos/rate-of-rise/firmware/esp32s3_feather_gateway/.esphome/build/creek-gateway-v2/.pioenvs/creek-gateway-v2/esp-idf/esp_hal_ieee802154/cmake_install.cmake")
endif()

if(NOT CMAKE_INSTALL_LOCAL_ONLY)
  # Include the install script for the subdirectory.
  include("C:/1_ProjectRepos/rate-of-rise/firmware/esp32s3_feather_gateway/.esphome/build/creek-gateway-v2/.pioenvs/creek-gateway-v2/esp-idf/esp_http_server/cmake_install.cmake")
endif()

if(NOT CMAKE_INSTALL_LOCAL_ONLY)
  # Include the install script for the subdirectory.
  include("C:/1_ProjectRepos/rate-of-rise/firmware/esp32s3_feather_gateway/.esphome/build/creek-gateway-v2/.pioenvs/creek-gateway-v2/esp-idf/wear_levelling/cmake_install.cmake")
endif()

if(NOT CMAKE_INSTALL_LOCAL_ONLY)
  # Include the install script for the subdirectory.
  include("C:/1_ProjectRepos/rate-of-rise/firmware/esp32s3_feather_gateway/.esphome/build/creek-gateway-v2/.pioenvs/creek-gateway-v2/esp-idf/fatfs/cmake_install.cmake")
endif()

if(NOT CMAKE_INSTALL_LOCAL_ONLY)
  # Include the install script for the subdirectory.
  include("C:/1_ProjectRepos/rate-of-rise/firmware/esp32s3_feather_gateway/.esphome/build/creek-gateway-v2/.pioenvs/creek-gateway-v2/esp-idf/idf_test/cmake_install.cmake")
endif()

if(NOT CMAKE_INSTALL_LOCAL_ONLY)
  # Include the install script for the subdirectory.
  include("C:/1_ProjectRepos/rate-of-rise/firmware/esp32s3_feather_gateway/.esphome/build/creek-gateway-v2/.pioenvs/creek-gateway-v2/esp-idf/ieee802154/cmake_install.cmake")
endif()

if(NOT CMAKE_INSTALL_LOCAL_ONLY)
  # Include the install script for the subdirectory.
  include("C:/1_ProjectRepos/rate-of-rise/firmware/esp32s3_feather_gateway/.esphome/build/creek-gateway-v2/.pioenvs/creek-gateway-v2/esp-idf/json/cmake_install.cmake")
endif()

if(NOT CMAKE_INSTALL_LOCAL_ONLY)
  # Include the install script for the subdirectory.
  include("C:/1_ProjectRepos/rate-of-rise/firmware/esp32s3_feather_gateway/.esphome/build/creek-gateway-v2/.pioenvs/creek-gateway-v2/esp-idf/nvs_sec_provider/cmake_install.cmake")
endif()

if(NOT CMAKE_INSTALL_LOCAL_ONLY)
  # Include the install script for the subdirectory.
  include("C:/1_ProjectRepos/rate-of-rise/firmware/esp32s3_feather_gateway/.esphome/build/creek-gateway-v2/.pioenvs/creek-gateway-v2/esp-idf/protobuf-c/cmake_install.cmake")
endif()

if(NOT CMAKE_INSTALL_LOCAL_ONLY)
  # Include the install script for the subdirectory.
  include("C:/1_ProjectRepos/rate-of-rise/firmware/esp32s3_feather_gateway/.esphome/build/creek-gateway-v2/.pioenvs/creek-gateway-v2/esp-idf/rt/cmake_install.cmake")
endif()

if(NOT CMAKE_INSTALL_LOCAL_ONLY)
  # Include the install script for the subdirectory.
  include("C:/1_ProjectRepos/rate-of-rise/firmware/esp32s3_feather_gateway/.esphome/build/creek-gateway-v2/.pioenvs/creek-gateway-v2/esp-idf/tcp_transport/cmake_install.cmake")
endif()

if(NOT CMAKE_INSTALL_LOCAL_ONLY)
  # Include the install script for the subdirectory.
  include("C:/1_ProjectRepos/rate-of-rise/firmware/esp32s3_feather_gateway/.esphome/build/creek-gateway-v2/.pioenvs/creek-gateway-v2/esp-idf/esp_driver_pcnt/cmake_install.cmake")
endif()

if(NOT CMAKE_INSTALL_LOCAL_ONLY)
  # Include the install script for the subdirectory.
  include("C:/1_ProjectRepos/rate-of-rise/firmware/esp32s3_feather_gateway/.esphome/build/creek-gateway-v2/.pioenvs/creek-gateway-v2/esp-idf/esp_driver_mcpwm/cmake_install.cmake")
endif()

if(NOT CMAKE_INSTALL_LOCAL_ONLY)
  # Include the install script for the subdirectory.
  include("C:/1_ProjectRepos/rate-of-rise/firmware/esp32s3_feather_gateway/.esphome/build/creek-gateway-v2/.pioenvs/creek-gateway-v2/esp-idf/esp_driver_i2s/cmake_install.cmake")
endif()

if(NOT CMAKE_INSTALL_LOCAL_ONLY)
  # Include the install script for the subdirectory.
  include("C:/1_ProjectRepos/rate-of-rise/firmware/esp32s3_feather_gateway/.esphome/build/creek-gateway-v2/.pioenvs/creek-gateway-v2/esp-idf/esp_driver_dac/cmake_install.cmake")
endif()

if(NOT CMAKE_INSTALL_LOCAL_ONLY)
  # Include the install script for the subdirectory.
  include("C:/1_ProjectRepos/rate-of-rise/firmware/esp32s3_feather_gateway/.esphome/build/creek-gateway-v2/.pioenvs/creek-gateway-v2/esp-idf/esp_driver_rmt/cmake_install.cmake")
endif()

if(NOT CMAKE_INSTALL_LOCAL_ONLY)
  # Include the install script for the subdirectory.
  include("C:/1_ProjectRepos/rate-of-rise/firmware/esp32s3_feather_gateway/.esphome/build/creek-gateway-v2/.pioenvs/creek-gateway-v2/esp-idf/esp_driver_twai/cmake_install.cmake")
endif()

if(NOT CMAKE_INSTALL_LOCAL_ONLY)
  # Include the install script for the subdirectory.
  include("C:/1_ProjectRepos/rate-of-rise/firmware/esp32s3_feather_gateway/.esphome/build/creek-gateway-v2/.pioenvs/creek-gateway-v2/esp-idf/driver/cmake_install.cmake")
endif()

if(NOT CMAKE_INSTALL_LOCAL_ONLY)
  # Include the install script for the subdirectory.
  include("C:/1_ProjectRepos/rate-of-rise/firmware/esp32s3_feather_gateway/.esphome/build/creek-gateway-v2/.pioenvs/creek-gateway-v2/esp-idf/touch_element/cmake_install.cmake")
endif()

if(NOT CMAKE_INSTALL_LOCAL_ONLY)
  # Include the install script for the subdirectory.
  include("C:/1_ProjectRepos/rate-of-rise/firmware/esp32s3_feather_gateway/.esphome/build/creek-gateway-v2/.pioenvs/creek-gateway-v2/esp-idf/usb/cmake_install.cmake")
endif()

if(NOT CMAKE_INSTALL_LOCAL_ONLY)
  # Include the install script for the subdirectory.
  include("C:/1_ProjectRepos/rate-of-rise/firmware/esp32s3_feather_gateway/.esphome/build/creek-gateway-v2/.pioenvs/creek-gateway-v2/esp-idf/bblanchon__arduinojson/cmake_install.cmake")
endif()

if(NOT CMAKE_INSTALL_LOCAL_ONLY)
  # Include the install script for the subdirectory.
  include("C:/1_ProjectRepos/rate-of-rise/firmware/esp32s3_feather_gateway/.esphome/build/creek-gateway-v2/.pioenvs/creek-gateway-v2/esp-idf/esp-libhelix-mp3/cmake_install.cmake")
endif()

if(NOT CMAKE_INSTALL_LOCAL_ONLY)
  # Include the install script for the subdirectory.
  include("C:/1_ProjectRepos/rate-of-rise/firmware/esp32s3_feather_gateway/.esphome/build/creek-gateway-v2/.pioenvs/creek-gateway-v2/esp-idf/arduino-esp32/cmake_install.cmake")
endif()

if(NOT CMAKE_INSTALL_LOCAL_ONLY)
  # Include the install script for the subdirectory.
  include("C:/1_ProjectRepos/rate-of-rise/firmware/esp32s3_feather_gateway/.esphome/build/creek-gateway-v2/.pioenvs/creek-gateway-v2/esp-idf/cbor/cmake_install.cmake")
endif()

if(NOT CMAKE_INSTALL_LOCAL_ONLY)
  # Include the install script for the subdirectory.
  include("C:/1_ProjectRepos/rate-of-rise/firmware/esp32s3_feather_gateway/.esphome/build/creek-gateway-v2/.pioenvs/creek-gateway-v2/esp-idf/esp-dsp/cmake_install.cmake")
endif()

if(NOT CMAKE_INSTALL_LOCAL_ONLY)
  # Include the install script for the subdirectory.
  include("C:/1_ProjectRepos/rate-of-rise/firmware/esp32s3_feather_gateway/.esphome/build/creek-gateway-v2/.pioenvs/creek-gateway-v2/esp-idf/esp-modbus/cmake_install.cmake")
endif()

if(NOT CMAKE_INSTALL_LOCAL_ONLY)
  # Include the install script for the subdirectory.
  include("C:/1_ProjectRepos/rate-of-rise/firmware/esp32s3_feather_gateway/.esphome/build/creek-gateway-v2/.pioenvs/creek-gateway-v2/esp-idf/esp-sr/cmake_install.cmake")
endif()

if(NOT CMAKE_INSTALL_LOCAL_ONLY)
  # Include the install script for the subdirectory.
  include("C:/1_ProjectRepos/rate-of-rise/firmware/esp32s3_feather_gateway/.esphome/build/creek-gateway-v2/.pioenvs/creek-gateway-v2/esp-idf/esp-zboss-lib/cmake_install.cmake")
endif()

if(NOT CMAKE_INSTALL_LOCAL_ONLY)
  # Include the install script for the subdirectory.
  include("C:/1_ProjectRepos/rate-of-rise/firmware/esp32s3_feather_gateway/.esphome/build/creek-gateway-v2/.pioenvs/creek-gateway-v2/esp-idf/esp-zigbee-lib/cmake_install.cmake")
endif()

if(NOT CMAKE_INSTALL_LOCAL_ONLY)
  # Include the install script for the subdirectory.
  include("C:/1_ProjectRepos/rate-of-rise/firmware/esp32s3_feather_gateway/.esphome/build/creek-gateway-v2/.pioenvs/creek-gateway-v2/esp-idf/esp_diag_data_store/cmake_install.cmake")
endif()

if(NOT CMAKE_INSTALL_LOCAL_ONLY)
  # Include the install script for the subdirectory.
  include("C:/1_ProjectRepos/rate-of-rise/firmware/esp32s3_feather_gateway/.esphome/build/creek-gateway-v2/.pioenvs/creek-gateway-v2/esp-idf/esp_diagnostics/cmake_install.cmake")
endif()

if(NOT CMAKE_INSTALL_LOCAL_ONLY)
  # Include the install script for the subdirectory.
  include("C:/1_ProjectRepos/rate-of-rise/firmware/esp32s3_feather_gateway/.esphome/build/creek-gateway-v2/.pioenvs/creek-gateway-v2/esp-idf/esp_hosted/cmake_install.cmake")
endif()

if(NOT CMAKE_INSTALL_LOCAL_ONLY)
  # Include the install script for the subdirectory.
  include("C:/1_ProjectRepos/rate-of-rise/firmware/esp32s3_feather_gateway/.esphome/build/creek-gateway-v2/.pioenvs/creek-gateway-v2/esp-idf/esp_insights/cmake_install.cmake")
endif()

if(NOT CMAKE_INSTALL_LOCAL_ONLY)
  # Include the install script for the subdirectory.
  include("C:/1_ProjectRepos/rate-of-rise/firmware/esp32s3_feather_gateway/.esphome/build/creek-gateway-v2/.pioenvs/creek-gateway-v2/esp-idf/esp_modem/cmake_install.cmake")
endif()

if(NOT CMAKE_INSTALL_LOCAL_ONLY)
  # Include the install script for the subdirectory.
  include("C:/1_ProjectRepos/rate-of-rise/firmware/esp32s3_feather_gateway/.esphome/build/creek-gateway-v2/.pioenvs/creek-gateway-v2/esp-idf/esp_rainmaker/cmake_install.cmake")
endif()

if(NOT CMAKE_INSTALL_LOCAL_ONLY)
  # Include the install script for the subdirectory.
  include("C:/1_ProjectRepos/rate-of-rise/firmware/esp32s3_feather_gateway/.esphome/build/creek-gateway-v2/.pioenvs/creek-gateway-v2/esp-idf/esp_rcp_update/cmake_install.cmake")
endif()

if(NOT CMAKE_INSTALL_LOCAL_ONLY)
  # Include the install script for the subdirectory.
  include("C:/1_ProjectRepos/rate-of-rise/firmware/esp32s3_feather_gateway/.esphome/build/creek-gateway-v2/.pioenvs/creek-gateway-v2/esp-idf/esp_schedule/cmake_install.cmake")
endif()

if(NOT CMAKE_INSTALL_LOCAL_ONLY)
  # Include the install script for the subdirectory.
  include("C:/1_ProjectRepos/rate-of-rise/firmware/esp32s3_feather_gateway/.esphome/build/creek-gateway-v2/.pioenvs/creek-gateway-v2/esp-idf/esp_secure_cert_mgr/cmake_install.cmake")
endif()

if(NOT CMAKE_INSTALL_LOCAL_ONLY)
  # Include the install script for the subdirectory.
  include("C:/1_ProjectRepos/rate-of-rise/firmware/esp32s3_feather_gateway/.esphome/build/creek-gateway-v2/.pioenvs/creek-gateway-v2/esp-idf/esp_wifi_remote/cmake_install.cmake")
endif()

if(NOT CMAKE_INSTALL_LOCAL_ONLY)
  # Include the install script for the subdirectory.
  include("C:/1_ProjectRepos/rate-of-rise/firmware/esp32s3_feather_gateway/.esphome/build/creek-gateway-v2/.pioenvs/creek-gateway-v2/esp-idf/json_generator/cmake_install.cmake")
endif()

if(NOT CMAKE_INSTALL_LOCAL_ONLY)
  # Include the install script for the subdirectory.
  include("C:/1_ProjectRepos/rate-of-rise/firmware/esp32s3_feather_gateway/.esphome/build/creek-gateway-v2/.pioenvs/creek-gateway-v2/esp-idf/json_parser/cmake_install.cmake")
endif()

if(NOT CMAKE_INSTALL_LOCAL_ONLY)
  # Include the install script for the subdirectory.
  include("C:/1_ProjectRepos/rate-of-rise/firmware/esp32s3_feather_gateway/.esphome/build/creek-gateway-v2/.pioenvs/creek-gateway-v2/esp-idf/lan867x/cmake_install.cmake")
endif()

if(NOT CMAKE_INSTALL_LOCAL_ONLY)
  # Include the install script for the subdirectory.
  include("C:/1_ProjectRepos/rate-of-rise/firmware/esp32s3_feather_gateway/.esphome/build/creek-gateway-v2/.pioenvs/creek-gateway-v2/esp-idf/libsodium/cmake_install.cmake")
endif()

if(NOT CMAKE_INSTALL_LOCAL_ONLY)
  # Include the install script for the subdirectory.
  include("C:/1_ProjectRepos/rate-of-rise/firmware/esp32s3_feather_gateway/.esphome/build/creek-gateway-v2/.pioenvs/creek-gateway-v2/esp-idf/espressif__mdns/cmake_install.cmake")
endif()

if(NOT CMAKE_INSTALL_LOCAL_ONLY)
  # Include the install script for the subdirectory.
  include("C:/1_ProjectRepos/rate-of-rise/firmware/esp32s3_feather_gateway/.esphome/build/creek-gateway-v2/.pioenvs/creek-gateway-v2/esp-idf/network_provisioning/cmake_install.cmake")
endif()

if(NOT CMAKE_INSTALL_LOCAL_ONLY)
  # Include the install script for the subdirectory.
  include("C:/1_ProjectRepos/rate-of-rise/firmware/esp32s3_feather_gateway/.esphome/build/creek-gateway-v2/.pioenvs/creek-gateway-v2/esp-idf/qrcode/cmake_install.cmake")
endif()

if(NOT CMAKE_INSTALL_LOCAL_ONLY)
  # Include the install script for the subdirectory.
  include("C:/1_ProjectRepos/rate-of-rise/firmware/esp32s3_feather_gateway/.esphome/build/creek-gateway-v2/.pioenvs/creek-gateway-v2/esp-idf/rmaker_common/cmake_install.cmake")
endif()

if(NOT CMAKE_INSTALL_LOCAL_ONLY)
  # Include the install script for the subdirectory.
  include("C:/1_ProjectRepos/rate-of-rise/firmware/esp32s3_feather_gateway/.esphome/build/creek-gateway-v2/.pioenvs/creek-gateway-v2/esp-idf/littlefs/cmake_install.cmake")
endif()

if(NOT CMAKE_INSTALL_LOCAL_ONLY)
  # Include the install script for the subdirectory.
  include("C:/1_ProjectRepos/rate-of-rise/firmware/esp32s3_feather_gateway/.esphome/build/creek-gateway-v2/.pioenvs/creek-gateway-v2/esp-idf/zorxx__multipart-parser/cmake_install.cmake")
endif()

if(NOT CMAKE_INSTALL_LOCAL_ONLY)
  # Include the install script for the subdirectory.
  include("C:/1_ProjectRepos/rate-of-rise/firmware/esp32s3_feather_gateway/.esphome/build/creek-gateway-v2/.pioenvs/creek-gateway-v2/esp-idf/src/cmake_install.cmake")
endif()

if(NOT CMAKE_INSTALL_LOCAL_ONLY)
  # Include the install script for the subdirectory.
  include("C:/1_ProjectRepos/rate-of-rise/firmware/esp32s3_feather_gateway/.esphome/build/creek-gateway-v2/.pioenvs/creek-gateway-v2/esp-idf/esp_adc/cmake_install.cmake")
endif()

if(NOT CMAKE_INSTALL_LOCAL_ONLY)
  # Include the install script for the subdirectory.
  include("C:/1_ProjectRepos/rate-of-rise/firmware/esp32s3_feather_gateway/.esphome/build/creek-gateway-v2/.pioenvs/creek-gateway-v2/esp-idf/esp_eth/cmake_install.cmake")
endif()

if(NOT CMAKE_INSTALL_LOCAL_ONLY)
  # Include the install script for the subdirectory.
  include("C:/1_ProjectRepos/rate-of-rise/firmware/esp32s3_feather_gateway/.esphome/build/creek-gateway-v2/.pioenvs/creek-gateway-v2/esp-idf/esp_http_client/cmake_install.cmake")
endif()

if(NOT CMAKE_INSTALL_LOCAL_ONLY)
  # Include the install script for the subdirectory.
  include("C:/1_ProjectRepos/rate-of-rise/firmware/esp32s3_feather_gateway/.esphome/build/creek-gateway-v2/.pioenvs/creek-gateway-v2/esp-idf/esp_https_ota/cmake_install.cmake")
endif()

if(NOT CMAKE_INSTALL_LOCAL_ONLY)
  # Include the install script for the subdirectory.
  include("C:/1_ProjectRepos/rate-of-rise/firmware/esp32s3_feather_gateway/.esphome/build/creek-gateway-v2/.pioenvs/creek-gateway-v2/esp-idf/esp_driver_touch_sens/cmake_install.cmake")
endif()

if(NOT CMAKE_INSTALL_LOCAL_ONLY)
  # Include the install script for the subdirectory.
  include("C:/1_ProjectRepos/rate-of-rise/firmware/esp32s3_feather_gateway/.esphome/build/creek-gateway-v2/.pioenvs/creek-gateway-v2/esp-idf/spiffs/cmake_install.cmake")
endif()

if(NOT CMAKE_INSTALL_LOCAL_ONLY)
  # Include the install script for the subdirectory.
  include("C:/1_ProjectRepos/rate-of-rise/firmware/esp32s3_feather_gateway/.esphome/build/creek-gateway-v2/.pioenvs/creek-gateway-v2/esp-idf/esp_hid/cmake_install.cmake")
endif()

if(NOT CMAKE_INSTALL_LOCAL_ONLY)
  # Include the install script for the subdirectory.
  include("C:/1_ProjectRepos/rate-of-rise/firmware/esp32s3_feather_gateway/.esphome/build/creek-gateway-v2/.pioenvs/creek-gateway-v2/esp-idf/framework-arduinoespressif32/cmake_install.cmake")
endif()

string(REPLACE ";" "\n" CMAKE_INSTALL_MANIFEST_CONTENT
       "${CMAKE_INSTALL_MANIFEST_FILES}")
if(CMAKE_INSTALL_LOCAL_ONLY)
  file(WRITE "C:/1_ProjectRepos/rate-of-rise/firmware/esp32s3_feather_gateway/.esphome/build/creek-gateway-v2/.pioenvs/creek-gateway-v2/esp-idf/install_local_manifest.txt"
     "${CMAKE_INSTALL_MANIFEST_CONTENT}")
endif()
