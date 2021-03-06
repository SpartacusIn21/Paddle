#  Copyright (c) 2018 PaddlePaddle Authors. All Rights Reserve.
#
#Licensed under the Apache License, Version 2.0 (the "License");
#you may not use this file except in compliance with the License.
#You may obtain a copy of the License at
#
#    http://www.apache.org/licenses/LICENSE-2.0
#
#Unless required by applicable law or agreed to in writing, software
#distributed under the License is distributed on an "AS IS" BASIS,
#WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
#See the License for the specific language governing permissions and
#limitations under the License.
import unittest
import paddle.v2.fluid as fluid
import paddle.v2.fluid.core as core
import numpy


class TestReorderLoDTensor(unittest.TestCase):
    num_seq = 5
    # [name, shape, lod_level] pair indicating data info of source and target
    data_desc = (['input', [9], 0], ['ref', [5], 1])

    @classmethod
    def setUpClass(cls):
        cls.set_program()

    @classmethod
    def set_program(cls):
        dat = fluid.layers.data(
            name=cls.data_desc[0][0], shape=cls.data_desc[0][1])
        dat.stop_gradient = False
        rank_dat = fluid.layers.data(
            name=cls.data_desc[1][0], shape=cls.data_desc[1][1])
        table = fluid.layers.lod_rank_table(rank_dat)
        new_dat = fluid.layers.reorder_lod_tensor_by_rank(
            x=dat, rank_table=table)
        loss = fluid.layers.reduce_sum(new_dat)
        fluid.backward.append_backward(loss=loss)
        cls.fetch_list = [new_dat, cls.data_desc[0][0] + '@GRAD']

    def run_program(self):
        outputs = []
        input_grads = []
        places = [core.CPUPlace()]
        if core.is_compile_gpu():
            places.append(core.CUDAPlace(0))
        for place in places:
            self.set_inputs(place)
            exe = fluid.Executor(place)
            output, input_grad = exe.run(fluid.default_main_program(),
                                         feed=self.inputs,
                                         fetch_list=self.fetch_list,
                                         return_numpy=False)
            outputs.append(output)
            input_grads.append(input_grad)
        self.actual_outputs = outputs
        self.actual_grads = input_grads

    def set_data(self):
        self.data = {}
        for desc in self.data_desc:
            data_name = desc[0]
            data_shape = desc[1]
            data_lod_level = desc[2]
            data_lod = []
            for i in range(data_lod_level):
                lod_level_i = numpy.random.randint(
                    low=1,
                    high=5,
                    size=self.num_seq if i == 0 else lod_level_i[-1])
                lod_level_i = [0] + numpy.cumsum(lod_level_i).tolist()
                data_lod.append(lod_level_i)
            data_value = numpy.random.random(
                size=[data_lod[-1][-1] if data_lod else self.num_seq
                      ] + data_shape).astype('float32')
            self.data[data_name] = (data_value, data_lod)

    def set_inputs(self, place):
        self.inputs = {}
        for desc in self.data_desc:
            tensor = fluid.Tensor()
            tensor.set(self.data[desc[0]][0], place)
            if self.data[desc[0]][1]:
                tensor.set_lod(self.data[desc[0]][1])
            self.inputs[desc[0]] = tensor

    def reorder(self):
        level = 0

        # compute the rank_table according to ref_lod
        ref_lod = self.data[self.data_desc[1][0]][1][level]
        rank_table = []  # list of (index, length)
        for i in range(len(ref_lod) - 1):
            rank_table.append((i, ref_lod[i + 1] - ref_lod[i]))
        rank_table = sorted(rank_table, lambda x, y: y[1] - x[1])

        # compute the input sequence info according to input_lod
        input_value, input_lod = self.data[self.data_desc[0][0]]

        input_table = []  # list of (offset, length, sub_lod)
        if input_lod:
            for i in range(len(input_lod[level]) - 1):
                start_idx = i
                end_idx = i + 1
                sub_lod = []
                for lod_level_i in input_lod[level:]:
                    sub_lod_i = []
                    for idx in range(start_idx, end_idx):
                        sub_lod_i.append(lod_level_i[idx + 1] - lod_level_i[
                            idx])
                    sub_lod.append(sub_lod_i)
                    start_idx = lod_level_i[start_idx]
                    end_idx = lod_level_i[end_idx]
                input_table.append((start_idx, end_idx - start_idx, sub_lod))
        else:
            input_table = [(i, 1, []) for i in range(len(rank_table))]

        # reorder by rank_table
        output_value = numpy.zeros_like(input_value)
        output_lod = []
        offset = 0
        for index, length in rank_table:
            input_seq_start = input_table[index][0]
            input_seq_len = input_table[index][1]
            input_seq_end = input_seq_start + input_seq_len
            output_value[offset:offset + input_seq_len] = input_value[
                input_seq_start:input_seq_end]
            offset += input_seq_len

            input_seq_sub_lod = input_table[index][2]
            if len(output_lod) == 0:
                output_lod = [[0] for i in input_seq_sub_lod]
            for i, sub_lod_i in enumerate(input_seq_sub_lod):
                for idx_sub in sub_lod_i:
                    output_lod[i].append(output_lod[i][-1] + idx_sub)
        return output_value, output_lod

    def test_reorder_lod_tensor(self):
        self.data_desc[0][-1] = 2  # input is lod_tensor
        self.set_data()
        self.run_program()
        # check output
        expect_output, expect_output_lod = self.reorder()
        for actual_output in self.actual_outputs:
            self.assertTrue(
                numpy.allclose(
                    numpy.array(actual_output), expect_output, atol=0.001))
            self.assertEqual(expect_output_lod, actual_output.lod())
        # check gradient
        expect_grad = numpy.ones_like(self.data[self.data_desc[0][0]][0])
        expect_grad_lod = self.data[self.data_desc[0][0]][1]
        for actual_grad in self.actual_grads:
            self.assertTrue(
                numpy.allclose(
                    numpy.array(actual_grad), expect_grad, atol=0.001))
            self.assertEqual(expect_grad_lod, actual_grad.lod())

    def test_reorder_tensor(self):
        self.data_desc[0][-1] = 0  # input is tensor
        self.set_data()
        self.run_program()
        # check output
        expect_output, expect_output_lod = self.reorder()
        for actual_output in self.actual_outputs:
            self.assertTrue(
                numpy.allclose(
                    numpy.array(actual_output), expect_output, atol=0.001))
            self.assertEqual(expect_output_lod, actual_output.lod())
        # check gradient
        expect_grad = numpy.ones_like(self.data[self.data_desc[0][0]][0])
        expect_grad_lod = self.data[self.data_desc[0][0]][1]
        for actual_grad in self.actual_grads:
            self.assertTrue(
                numpy.allclose(
                    numpy.array(actual_grad), expect_grad, atol=0.001))
            self.assertEqual(expect_grad_lod, actual_grad.lod())

        # compare outputs between LodTensors with explicit and implicit lod
        # use the same data but set the input lod explicitly
        input_lod = [[
            i for i in range(len(self.data[self.data_desc[0][0]][0]) + 1)
        ]]
        self.inputs[self.data_desc[0][0]].set_lod(input_lod)
        # preserve the output of LodTensor with implicit lod to compare
        expect_output = [
            numpy.array(actual_output) for actual_output in self.actual_outputs
        ]
        self.run_program()
        for actual_output in self.actual_outputs:
            self.assertTrue(
                numpy.allclose(
                    numpy.array(actual_output), expect_output, atol=0.001))


if __name__ == '__main__':
    unittest.main()
